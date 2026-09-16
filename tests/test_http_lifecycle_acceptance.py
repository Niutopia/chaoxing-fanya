"""Cross-process HTTP acceptance with real scheduling and isolated platform IO."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]


def eventually(read, matches, timeout=15):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if matches(value):
            return value
        time.sleep(0.04)
    pytest.fail(f"Timed out waiting for acceptance condition: {value!r}")


class Server:
    def __init__(self, directory):
        self.directory = directory
        self.ready = directory / "ready"
        self.process = None

    def start(self):
        self.ready.unlink(missing_ok=True)
        self.output = (self.directory / "server-output.log").open("a")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/support/lifecycle_server.py"),
             str(self.directory), str(self.ready)], cwd=ROOT,
            env={**os.environ, "CHAOXING_DATA_DIR": str(self.directory)},
            stdout=self.output, stderr=subprocess.STDOUT,
        )
        eventually(lambda: (self.process.poll(), self.ready.exists()),
                   lambda state: state[0] is not None or state[1])
        assert self.process.poll() is None, (self.directory / "server-output.log").read_text()
        self.url = self.ready.read_text()
        assert self.api("GET", "/api/health")["service"] == "chaoxing-web"

    def stop(self):
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=5)
            self.output.close()
            self.process = None

    def api(self, method, path, body=None, status=200):
        reply = requests.request(method, self.url + path, json=body, timeout=5)
        assert reply.status_code == status, reply.text
        return reply.json()["data"]

    def state(self):
        return json.loads((self.directory / "platform.json").read_text())

    def task(self, task_id):
        return self.api("GET", f"/api/tasks/{task_id}")

    def terminal(self, task_id):
        return eventually(lambda: self.task(task_id),
                          lambda task: task["state"] not in {"running", "stopping"})


@pytest.mark.parametrize("interruption", ["cancel", "kill"])
def test_submission_recovery_cancel_and_process_restart(tmp_path, interruption):
    server = Server(tmp_path)
    server.start()
    try:
        account = server.api("POST", "/api/accounts", {
            "name": "隔离验收账号", "username": "acceptance-user", "password": "acceptance-password"}, status=201)
        account_id = account["id"]
        server.api("POST", f"/api/accounts/{account_id}/verify", {})
        courses = server.api("GET", f"/api/accounts/{account_id}/courses?refresh=true")
        assert "course-acceptance" in json.dumps(courses)
        server.api("PUT", "/api/settings/answer-connection", {
            "enabled": True, "base_url": "https://acceptance.invalid/v1",
            "model": "acceptance", "api_key": "acceptance-api-key"})
        server.api("POST", "/api/settings/answer-connection/test", {})
        server.api("PATCH", f"/api/accounts/{account_id}/preferences", {
            "jobs": 1, "answer_enabled": True, "answer_auto_submit": True})

        def start():
            return server.api("POST", f"/api/accounts/{account_id}/tasks",
                              {"course_ids": ["course-acceptance"]}, status=201)["id"]

        # First POST is accepted remotely, but the response is lost locally.
        lost_id = start()
        assert server.terminal(lost_id)["state"] == "failed"
        assert server.state()["posts"] == ["work-first"]

        # Resuming must read the submitted page instead of sending another POST.
        interrupted_id = start()
        eventually(server.state, lambda state: any(e["event"] == "answer_wait" for e in state["events"]))
        running = eventually(lambda: server.task(interrupted_id),
                             lambda task: task["stats"].get("completed_chapters") == 1)
        assert server.state()["posts"] == ["work-first"]
        if interruption == "cancel":
            server.api("POST", f"/api/tasks/{interrupted_id}/cancel", {})
            assert server.terminal(interrupted_id)["state"] == "stopped"
            # Even a provider returning after cancellation must not submit.
            (tmp_path / "release-answer").touch()
            eventually(server.state, lambda state: any(e["event"] == "answer_return" for e in state["events"]))
            time.sleep(0.15)
            assert server.state()["posts"] == ["work-first"]
        server.stop()
        server.start()
        restored = server.task(interrupted_id)
        assert restored["state"] == ("failed" if interruption == "kill" else "stopped")
        assert restored["stats"]["completed_chapters"] == running["stats"]["completed_chapters"] == 1
        assert restored["started_at"] == running["started_at"]
        if interruption == "kill":
            assert "服务重启" in restored["error"]
        assert server.task(lost_id)["state"] == "failed"
        logs = server.api("GET", f"/api/tasks/{interrupted_id}/logs?after=0")["items"]
        assert logs

        (tmp_path / "release-answer").touch()
        complete_id = start()
        complete = server.terminal(complete_id)
        assert complete["state"] == "completed", complete
        assert complete["stats"]["completed_chapters"] == complete["stats"]["total_chapters"] == 2
        assert server.state()["posts"] == ["work-first", "work-second"]
        server.api("POST", f"/api/tasks/{complete_id}/answer-report", {}, status=202)
        details = eventually(lambda: server.api("GET", f"/api/tasks/{complete_id}/details"),
                             lambda details: details["answer_report"]["status"] != "running")
        assert details["answer_report"]["status"] == "completed"
        assert details["answer_report"]["counts"]["answer_correct_questions"] == 2
        assert server.state()["posts"] == ["work-first", "work-second"]
        server.stop()
        server.start()
        assert server.api("GET", f"/api/tasks/{complete_id}/details")["answer_report"] == details["answer_report"]
        assert len(server.api("GET", "/api/tasks")) == 3
        diagnostic = (tmp_path / "chaoxing.log").read_text()
        assert '"event":"request_exception","action":"submit"' in diagnostic
        assert '"event":"existing_submission_preserved"' in diagnostic
        assert '"event":"refresh_completed"' in diagnostic
        public_logs = server.api("GET", f"/api/tasks/{complete_id}/logs?after=0")["items"]
        assert any('平台已接收该测验' in item['message'] for item in public_logs)
        assert all('测验操作 {' not in item['message'] for item in public_logs)
    finally:
        server.stop()
