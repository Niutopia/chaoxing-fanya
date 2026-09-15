"""Black-box regression coverage for durable web-task monitor history."""

from __future__ import annotations

import itertools
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from webapp import create_app
from webapp.models import (
    AccountAuth,
    AccountPreferences,
    ResolvedAnswerConnection,
)


PASSWORD = "task-password-never-store-in-monitor"
COOKIE = "task-cookie-never-store-in-monitor"
API_KEY = "task-api-key-never-store-in-monitor"


class BlockingRunner:
    """Keep a task alive long enough to emulate an abrupt app restart."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, context) -> None:
        self.entered.set()
        self.release.wait(timeout=5)


class CompletedRunner:
    """A deterministic runner that reports terminal monitor data."""

    def run(self, context) -> None:
        context.reporter.set_current(
            course="completed-course",
            chapter="completed-chapter",
            task="completed-task",
        )
        context.reporter.set_courses(
            [{"id": "completed-course", "title": "Completed course"}]
        )
        context.reporter.set_active_jobs(
            {"job-1": {"title": "Completed job", "progress": 1}}
        )
        context.reporter.set_counts(progress=1, total=1, completed_tasks=1)
        context.reporter.append_log("completed checkpoint", level="info")


@contextmanager
def app_lifetime(data_dir: Path, runner) -> Iterator:
    """Create an app and deterministically tear down its global log sink."""

    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": data_dir,
            "TASK_RUNNER": runner,
        }
    )
    manager = app.extensions["task_manager"]
    try:
        yield app
    finally:
        # Release every worker before unregistering this app's manager.  The
        # finalizer is explicit because the routing sink is process-global and
        # tests intentionally create multiple Flask apps against one directory.
        release = getattr(runner, "release", None)
        if isinstance(release, threading.Event):
            release.set()
        for snapshot in manager.list_tasks():
            manager.wait(snapshot.id, timeout=2)
        finalizer = app.extensions.get("task_log_sink_finalizer")
        if finalizer is not None and finalizer.alive:
            finalizer()


def _new_task(manager, account_id: str, *, password: str = PASSWORD):
    return manager.start(
        account_id=account_id,
        course_ids=["course-1"],
        preferences=AccountPreferences(selected_course_ids=["course-1"]),
        auth=AccountAuth(
            username="monitor-user",
            password=password,
            cookies={"session": COOKIE},
        ),
        answer=ResolvedAnswerConnection(
            enabled=True,
            has_api_key=True,
            api_key=API_KEY,
        ),
    )


def _create_account(app):
    return app.extensions["services"]["store"].create_account(
        "Persistence account",
        "monitor-user",
        PASSWORD,
    )


def test_running_task_is_interrupted_and_history_survives_app_rebuild(tmp_path):
    data_dir = tmp_path / "data"
    first_runner = BlockingRunner()

    with app_lifetime(data_dir, first_runner) as first_app:
        account = _create_account(first_app)
        first_manager = first_app.extensions["task_manager"]
        task = _new_task(first_manager, account.id)
        assert first_runner.entered.wait(timeout=1)

        reporter = first_manager.get_reporter(task.id)
        reporter.set_current(
            course="course-title",
            chapter="chapter-title",
            task="task-title",
        )
        reporter.set_courses(
            [{"id": "course-1", "title": "Course title", "progress": 2}]
        )
        reporter.set_active_jobs(
            {"job-1": {"title": "Current job", "progress": 2}}
        )
        reporter.set_counts(progress=2, total=3, completed_tasks=2)
        reporter.append_log("checkpoint before restart", level="info", timestamp=100.0)

        before_restart = first_manager.get_snapshot(task.id)
        store = first_app.extensions["services"]["store"]
        persisted = store.load_web_tasks(limit=10)
        assert [item["snapshot"]["id"] for item in persisted] == [task.id]
        assert any(
            item["message"] == "checkpoint before restart"
            for item in persisted[0]["logs"]
        )

        # A second app instance reads the same durable monitor record while the
        # first process still owns the blocking worker.
        with app_lifetime(data_dir, BlockingRunner()) as second_app:
            second_client = second_app.test_client()
            task_response = second_client.get(f"/api/tasks/{task.id}")
            details_response = second_client.get(f"/api/tasks/{task.id}/details")
            logs_response = second_client.get(f"/api/tasks/{task.id}/logs?after=0")

            assert task_response.status_code == 200
            restored = task_response.get_json()["data"]
            assert restored["id"] == task.id
            assert restored["account_id"] == account.id
            assert restored["state"] == "failed"
            assert restored["error"]
            assert "重启" in restored["error"]
            assert "中断" in restored["error"]
            assert restored["started_at"] == before_restart.started_at
            assert restored["finished_at"] is not None
            assert restored["progress"] == before_restart.progress
            assert restored["total"] == before_restart.total
            assert restored["current_course"] == before_restart.current_course
            assert restored["current_chapter"] == before_restart.current_chapter
            assert restored["current_task"] == before_restart.current_task
            assert restored["stats"] == before_restart.stats

            assert details_response.status_code == 200
            details = details_response.get_json()["data"]
            assert details["courses"] == [
                {"id": "course-1", "title": "Course title", "progress": 2}
            ]
            assert details["counts"] == {
                "progress": 2,
                "total": 3,
                "completed_tasks": 2,
            }
            # A restored worker cannot have live jobs; durable course/count
            # history remains available while active work is normalized away.
            assert details["active_jobs"] == {}

            assert logs_response.status_code == 200
            messages = [
                item["message"]
                for item in logs_response.get_json()["data"]["items"]
            ]
            assert "checkpoint before restart" in messages
            assert any("重启" in message and "中断" in message for message in messages)


def test_completed_task_is_unchanged_after_app_rebuild(tmp_path):
    data_dir = tmp_path / "data"

    with app_lifetime(data_dir, CompletedRunner()) as first_app:
        account = _create_account(first_app)
        first_manager = first_app.extensions["task_manager"]
        task = _new_task(first_manager, account.id)
        assert first_manager.wait(task.id, timeout=1)
        expected_snapshot = first_manager.get_snapshot(task.id)
        expected_details = first_manager.get_details(task.id)
        expected_logs = first_manager.get_logs(task.id, after=0)
        assert expected_snapshot.state == "completed"

        with app_lifetime(data_dir, CompletedRunner()) as second_app:
            second_manager = second_app.extensions["task_manager"]
            assert second_manager.get_snapshot(task.id) == expected_snapshot
            assert second_manager.get_details(task.id) == expected_details
            assert second_manager.get_logs(task.id, after=0) == expected_logs


def test_task_json_and_logs_do_not_contain_credentials(tmp_path):
    data_dir = tmp_path / "data"
    runner = BlockingRunner()

    with app_lifetime(data_dir, runner) as app:
        account = _create_account(app)
        manager = app.extensions["task_manager"]
        task = _new_task(manager, account.id)
        assert runner.entered.wait(timeout=1)

        reporter = manager.get_reporter(task.id)
        reporter.set_current(course=f"course-{PASSWORD}-{COOKIE}-{API_KEY}")
        reporter.set_courses(
            [
                {
                    "id": "course-1",
                    "password": PASSWORD,
                    "cookie": COOKIE,
                    "api_key": API_KEY,
                }
            ]
        )
        reporter.set_active_jobs(
            {
                "job-1": {
                    "password": PASSWORD,
                    "cookie": COOKIE,
                    "api_key": API_KEY,
                }
            }
        )
        reporter.append_log(
            f"credentials {PASSWORD} {COOKIE} {API_KEY}",
            level="warning",
            timestamp=101.0,
        )

        # Inspect only the durable task monitor tables.  Account credentials
        # live in separate encrypted tables; this assertion covers the JSON
        # and log surfaces that are restored into the monitor UI.
        with sqlite3.connect(app.extensions["services"]["store"].db_path) as connection:
            values = [
                row[0]
                for row in connection.execute(
                    "SELECT snapshot_json FROM web_tasks WHERE id = ?",
                    (task.id,),
                )
            ]
            values.extend(
                row[0]
                for row in connection.execute(
                    "SELECT details_json FROM web_tasks WHERE id = ?",
                    (task.id,),
                )
            )
            values.extend(
                row[0]
                for row in connection.execute(
                    "SELECT message FROM web_task_logs WHERE task_id = ?",
                    (task.id,),
                )
            )
        task_storage = "\n".join(values)
        assert task_storage
        for secret in (PASSWORD, COOKIE, API_KEY):
            assert secret not in task_storage


def test_same_account_keeps_terminal_history_and_lists_newest_first(tmp_path, monkeypatch):
    # Distinct deterministic start times make the ordering assertion
    # independent of wall-clock resolution on the host filesystem/CI runner.
    ticks = itertools.count()
    import webapp.task_manager as task_manager_module

    monkeypatch.setattr(
        task_manager_module.time,
        "time",
        lambda: 1_000.0 + next(ticks),
    )

    data_dir = tmp_path / "data"
    with app_lifetime(data_dir, CompletedRunner()) as app:
        account = _create_account(app)
        manager = app.extensions["task_manager"]
        first = _new_task(manager, account.id)
        assert manager.wait(first.id, timeout=1)
        second = _new_task(manager, account.id)
        assert manager.wait(second.id, timeout=1)

        listed = manager.list_tasks()
        assert [item.id for item in listed] == [second.id, first.id]
        assert manager.get_snapshot(first.id).state == "completed"
        assert manager.get_snapshot(second.id).state == "completed"

        with sqlite3.connect(app.extensions["services"]["store"].db_path) as connection:
            persisted_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM web_tasks ORDER BY started_at DESC, id DESC"
                )
            ]
        assert persisted_ids[:2] == [second.id, first.id]
