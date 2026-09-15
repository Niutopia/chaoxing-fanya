"""Focused RED/GREEN coverage for the final unified fix wave.

These tests deliberately exercise the seams called out by the final review:
the task reporter, persistence boundaries, route error envelopes, and the
process-wide log sink.  They stay deterministic and never contact Chaoxing or
the answer service.
"""

from __future__ import annotations

import threading
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
import main

from webapp import create_app
from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences, ResolvedAnswerConnection
from webapp.study_runner import ChaoxingStudyRunner, StudyRunError
from webapp.store import SQLiteStore
from webapp.task_logging import install_task_log_sink, remove_task_log_sink
from webapp.task_manager import TaskManager, TaskNotRunning


def test_worker_context_wrapper_forwards_target_config_keyword():
    target_config = {"cancel_event": threading.Event(), "marker": "forwarded"}

    def target(*, config):
        return config

    assert main._run_worker_with_context(
        {"ocr_config": {}}, target, config=target_config
    ) is target_config


@pytest.fixture
def store(app):
    return app.extensions["services"]["store"]


class MonitorReporter:
    def __init__(self) -> None:
        self.courses: list = []
        self.jobs: list = []
        self.counts: list[dict] = []
        self.current: list[dict] = []

    def set_courses(self, courses):
        self.courses.append(courses)

    def set_active_jobs(self, jobs):
        self.jobs.append(jobs)

    def set_counts(self, **counts):
        self.counts.append(dict(counts))

    def set_current(self, **current):
        self.current.append(dict(current))


class MonitorEngine:
    def __init__(self, common_config):
        self.common_config = common_config
        self.rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)
        self.session = SimpleNamespace(cookies=SimpleNamespace(get=lambda *_: "uid"))

    def login(self, login_with_cookies=False):
        return {"status": True}

    def get_course_list(self):
        return [{"courseId": "math", "title": "高等数学", "clazzId": "c", "cpi": "p"}]

    def get_course_point(self, *_args):
        return {
            "points": [
                {"id": "chapter-1", "title": "第一章", "has_finished": False, "jobCount": 2}
            ]
        }

    def get_job_list(self, *_args):
        return ([{"id": "job-a", "title": "视频"}, {"id": "job-b", "title": "测验"}], {"notOpen": False})

    def process_course(self, course, config):
        point = self.get_course_point()["points"][0]
        callbacks = config
        callback = callbacks.get("course_points_callback")
        if callable(callback):
            callback(course, [point])
        callback = callbacks.get("course_start_callback")
        if callable(callback):
            callback(course)
        callbacks["chapter_start_callback"](course, point)
        jobs, _ = self.get_job_list(course, point)
        callback = callbacks.get("job_list_callback")
        if callable(callback):
            callback(course, point, jobs)
        for job in jobs:
            callback = callbacks.get("job_start_callback")
            if callable(callback):
                callback(course, point, job)
            if job["id"] == "job-a":
                callback = callbacks.get("video_progress_callback")
                if callable(callback):
                    callback(course, job, 5, 10)
            callback = callbacks.get("job_done_callback")
            if callable(callback):
                callback(course, point, job, "completed")
        callbacks["chapter_done_callback"](course, point)
        callback = callbacks.get("course_done_callback")
        if callable(callback):
            callback(course)


def _runner_context(course_ids: list[str], reporter=None):
    reporter = reporter or MonitorReporter()
    return SimpleNamespace(
        task_id="task-final-fix",
        account_id="account-final-fix",
        course_ids=course_ids,
        preferences=AccountPreferences(selected_course_ids=course_ids),
        auth=AccountAuth(username="user", password="password", cookies={}),
        answer=None,
        answer_semaphore=None,
        cancel_event=threading.Event(),
        reporter=reporter,
    )


def test_runner_reports_real_course_tree_counts_and_active_jobs(tmp_path):
    reporter = MonitorReporter()
    context = _runner_context(["math"], reporter)
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=MonitorEngine)

    runner.run(context)

    assert reporter.courses[-1] == [
        {
            "id": "math",
            "title": "高等数学",
            "status": "completed",
            "chapters": [
                {
                    "id": "chapter-1",
                    "title": "第一章",
                    "status": "completed",
                    "jobs": [
                        {"id": "job-a", "title": "视频", "status": "completed"},
                        {"id": "job-b", "title": "测验", "status": "completed"},
                    ],
                }
            ],
        }
    ]
    assert reporter.jobs[-1] == {}
    assert any(
        jobs.get("math:chapter-1:job-a", {}).get("progress") == 5
        for jobs in reporter.jobs
    )
    assert reporter.counts[-1] == {
        "completed_courses": 1,
        "total_courses": 1,
        "completed_chapters": 1,
        "total_chapters": 1,
        "completed_tasks": 2,
        "total_tasks": 2,
    }


def test_task_log_sink_unregisters_one_manager_without_orphaning_other():
    from webapp.task_logging import _route_record, unregister_task_log_sink

    release = threading.Event()

    def runner(_context):
        release.wait(timeout=2)

    manager_a = TaskManager(runner=runner)
    manager_b = TaskManager(runner=runner)
    inputs = dict(
        course_ids=["course"],
        preferences=AccountPreferences(selected_course_ids=["course"]),
        auth=AccountAuth(username="user", password="", cookies={}),
        answer=None,
    )
    task_a = manager_a.start(account_id="a", **inputs)
    task_b = manager_b.start(account_id="b", **inputs)

    remove_task_log_sink()
    try:
        sink_a = install_task_log_sink(manager_a)
        assert install_task_log_sink(manager_a) == sink_a
        assert install_task_log_sink(manager_b) == sink_a
        unregister_task_log_sink(manager_a)
        _route_record(SimpleNamespace(record={"extra": {"task_id": task_a.id}, "message": "ignored", "level": "INFO"}))
        _route_record(SimpleNamespace(record={"extra": {"task_id": task_b.id}, "message": "kept", "level": "INFO"}))
        assert manager_a.get_logs(task_a.id).items == []
        assert [entry.message for entry in manager_b.get_logs(task_b.id).items] == ["kept"]
    finally:
        remove_task_log_sink()
        release.set()


class RacingCancelManager:
    def __init__(self):
        self.lock = threading.RLock()

    def list_tasks(self):
        return []

    def get_snapshot(self, _task_id):
        return SimpleNamespace(state="running")

    def cancel(self, _task_id):
        raise TaskNotRunning("task finished during cancellation")


def test_cancel_terminal_race_returns_stable_task_not_running(client, app):
    manager = RacingCancelManager()
    app.extensions["task_manager"] = manager
    app.extensions["account_task_lock"] = manager.lock
    app.extensions["services"]["task_manager"] = manager
    response = client.post("/api/tasks/racing/cancel")
    assert response.status_code == 409
    assert response.get_json() == {
        "status": False,
        "msg": "Task is not running",
        "code": "task_not_running",
    }


def test_credential_material_changes_reset_verification_and_auth_mode_clears_secret(store):
    profile = store.create_account(
        "账号",
        "old-user",
        "old-password",
        auth_mode="password",
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
        cookies={"sid": "old-cookie"},
    )
    changed = store.update_account(profile.id, username="new-user")
    assert changed.verification_status == "unverified"
    assert changed.last_verified_at is None

    store.update_account(
        profile.id,
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )
    store.update_account(profile.id, auth_mode="cookies", cookies={"sid": "new-cookie"})
    switched = store.get_account(profile.id)
    auth = store.get_account_auth(profile.id)
    assert switched.auth_mode == "cookies"
    assert switched.has_secret is False
    assert switched.has_cookies is True
    assert auth.password == ""
    assert auth.cookies == {"sid": "new-cookie"}
    assert switched.verification_status == "unverified"
    assert switched.last_verified_at is None


def test_repeating_same_credential_material_preserves_verification(store):
    profile = store.create_account(
        "账号",
        "user",
        "password",
        auth_mode="password",
        cookies={"sid": "cookie"},
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )
    unchanged = store.update_account(
        profile.id,
        username="user",
        password="password",
        auth_mode="password",
    )
    assert unchanged.verification_status == "valid"
    assert unchanged.last_verified_at == "2026-09-14T00:00:00+00:00"
    unchanged = store.update_account(
        profile.id,
        auth_mode="cookies",
        cookies={"sid": "cookie"},
    )
    assert unchanged.verification_status == "unverified"


def test_legacy_cookie_only_account_migrates_to_cookie_auth_mode(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "chaoxing-web.sqlite3"
    secret_box = SecretBox(data_dir)
    cookie_token = secret_box.encrypt(json.dumps({"sid": "legacy-cookie"}))
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            password_token TEXT,
            cookies_token TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            last_verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO accounts
            (id, name, username, password_token, cookies_token, enabled,
             verification_status, last_verified_at, created_at, updated_at)
        VALUES ('legacy', '旧账号', 'legacy-user', NULL, ?, 1, 'unverified', NULL, 'now', 'now')
        """,
        (cookie_token,),
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(database_path, secret_box)
    profile = store.get_account("legacy")
    auth = store.get_account_auth("legacy")
    assert profile.auth_mode == "cookies"
    assert auth.auth_mode == "cookies"
    assert auth.cookies == {"sid": "legacy-cookie"}


def test_blank_password_and_cookie_editor_values_preserve_existing_credentials(client, store):
    profile = store.create_account(
        "账号",
        "user",
        "password",
        auth_mode="password",
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )
    password_response = client.patch(f"/api/accounts/{profile.id}", json={"password": ""})
    cookie_response = client.patch(f"/api/accounts/{profile.id}", json={"cookies": ""})
    assert password_response.status_code == 200
    assert cookie_response.status_code == 200
    auth = store.get_account_auth(profile.id)
    assert auth.password == "password"
    assert auth.cookies == {}
    assert store.get_account(profile.id).verification_status == "valid"

    cookie_profile = store.create_account(
        "Cookie 账号",
        "cookie-user",
        None,
        auth_mode="cookies",
        cookies={"sid": "cookie"},
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )
    password_response = client.patch(
        f"/api/accounts/{cookie_profile.id}", json={"password": ""}
    )
    cookie_response = client.patch(
        f"/api/accounts/{cookie_profile.id}", json={"cookies": ""}
    )
    assert password_response.status_code == 200
    assert cookie_response.status_code == 200
    cookie_auth = store.get_account_auth(cookie_profile.id)
    assert cookie_auth.password == ""
    assert cookie_auth.cookies == {"sid": "cookie"}
    assert store.get_account(cookie_profile.id).verification_status == "valid"


class UnreadableTaskManager:
    lock = threading.RLock()

    def list_tasks(self):
        raise RuntimeError("state unavailable")


def test_settings_mutation_fails_closed_when_task_state_cannot_be_read(tmp_path):
    manager = UnreadableTaskManager()
    app = create_app({"DATA_DIR": tmp_path, "TASK_MANAGER": manager})
    client = app.test_client()
    before = app.extensions["services"]["store"].get_answer_connection()
    response = client.put(
        "/api/settings/answer-connection",
        json={"base_url": "http://localhost:8849/v1"},
    )
    after = app.extensions["services"]["store"].get_answer_connection()
    assert response.status_code == 503
    assert response.get_json()["code"] == "settings_state_unavailable"
    assert after == before


def test_account_auth_repr_does_not_expose_identity_or_secrets():
    auth = AccountAuth(
        username="private-user",
        password="private-password",
        cookies={"sid": "private-cookie"},
    )
    rendered = repr(auth)
    assert "private-user" not in rendered
    assert "private-password" not in rendered
    assert "private-cookie" not in rendered


class CourseSelectionEngine(MonitorEngine):
    processed = False

    def process_course(self, course, config):
        type(self).processed = True


class WrappedCourseCatalogEngine(MonitorEngine):
    def get_course_list(self):
        return {
            "data": {
                "courses": [
                    {
                        "courseId": "math",
                        "title": "高等数学",
                        "clazzId": "c",
                        "cpi": "p",
                    }
                ]
            }
        }


class RaisingCourseEngine(MonitorEngine):
    def process_course(self, _course, _config):
        raise RuntimeError("course exploded")


def test_runner_normalizes_wrapped_course_catalog_shape(tmp_path):
    runner = ChaoxingStudyRunner(
        data_dir=tmp_path,
        engine_factory=WrappedCourseCatalogEngine,
    )
    runner.run(_runner_context(["math"]))


def test_runner_marks_course_failed_when_engine_raises(tmp_path):
    reporter = MonitorReporter()
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=RaisingCourseEngine)
    with pytest.raises(StudyRunError, match="course exploded"):
        runner.run(_runner_context(["math"], reporter))
    assert reporter.courses[-1][0]["status"] == "failed"


def test_process_chapter_normalizes_wrapped_job_response():
    import main

    class ChapterEngine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)

        def get_job_list(self, _course, _point):
            return {"data": {"jobs": [], "notOpen": False}}

    seen = []
    result = main.process_chapter(
        ChapterEngine(),
        {"courseId": "math", "title": "高等数学"},
        {"id": "chapter", "title": "第一章", "has_finished": False},
        1.0,
        {
            "jobs": 1,
            "notopen_action": "continue",
            "job_list_callback": lambda *_args: seen.append("jobs"),
            "chapter_done_callback": lambda *_args: seen.append("done"),
        },
    )
    assert result is main.ChapterResult.SUCCESS
    assert seen == ["jobs", "done"]


def test_finished_chapter_reports_completion_without_fetching_jobs():
    import main

    class FinishedEngine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)

        def get_job_list(self, *_args):
            raise AssertionError("finished chapters do not need a job request")

    seen = []
    result = main.process_chapter(
        FinishedEngine(),
        {"courseId": "math", "title": "高等数学"},
        {"id": "chapter", "title": "第一章", "has_finished": True},
        1.0,
        {"chapter_done_callback": lambda *_args: seen.append("done")},
    )
    assert result is main.ChapterResult.SUCCESS
    assert seen == ["done"]


def test_process_course_reports_failed_course_without_marking_it_complete(monkeypatch):
    import main

    class FailedProcessor:
        def __init__(self, *_args):
            self.failed_tasks = [object()]

        def run(self):
            return None

    monkeypatch.setattr(main, "JobProcessor", FailedProcessor)
    monkeypatch.setattr(main, "logger", SimpleNamespace(info=lambda *_args, **_kwargs: None))
    events = []
    main.process_course(
        SimpleNamespace(get_course_point=lambda *_args: {"points": []}),
        {"courseId": "math", "clazzId": "c", "cpi": "p", "title": "高等数学"},
        {
            "speed": 1.0,
            "jobs": 1,
            "notopen_action": "continue",
            "course_done_callback": lambda *_args: events.append("done"),
            "course_failed_callback": lambda *_args: events.append("failed"),
        },
    )
    assert events == ["failed"]


def test_runner_rejects_explicit_unknown_course_ids_without_fallback(tmp_path):
    context = _runner_context(["missing-course"])
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=CourseSelectionEngine)
    with pytest.raises(StudyRunError) as captured:
        runner.run(context)
    assert captured.value.code == "course_selection_invalid"
    assert CourseSelectionEngine.processed is False


class BrokenListManager:
    lock = threading.RLock()

    def list_tasks(self):
        raise RuntimeError("do not leak this exception")


def test_unhandled_api_exception_uses_fixed_internal_error_envelope(tmp_path):
    app = create_app({"DATA_DIR": tmp_path, "TASK_MANAGER": BrokenListManager()})
    response = app.test_client().get("/api/tasks")
    assert response.status_code == 500
    assert response.get_json() == {
        "status": False,
        "msg": "Internal server error",
        "code": "internal_error",
    }
    assert "do not leak" not in response.get_data(as_text=True)


class BlockingRunner:
    def __init__(self):
        self.release = threading.Event()

    def run(self, _context):
        self.release.wait(timeout=2)


def test_explicit_selection_is_persisted_before_capacity_rejection(tmp_path):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    app = create_app({"DATA_DIR": tmp_path, "TASK_MANAGER": manager})
    client = app.test_client()
    store = app.extensions["services"]["store"]
    first = store.create_account("第一", "1", "one")
    second = store.create_account("第二", "2", "two")
    assert client.post(f"/api/accounts/{first.id}/tasks", json={"course_ids": ["first"]}).status_code == 201
    rejected = client.post(
        f"/api/accounts/{second.id}/tasks", json={"course_ids": ["explicit-before-reject"]}
    )
    assert rejected.status_code == 409
    assert rejected.get_json()["code"] == "task_limit_reached"
    assert store.get_preferences(second.id).selected_course_ids == ["explicit-before-reject"]
    runner.release.set()


def test_account_creation_remains_two_stage_without_remote_verification(app):
    class VerifyMustNotRun:
        def verify(self, _account_id):
            raise AssertionError("account creation must not verify remotely")

    app.extensions["services"]["account_service"] = VerifyMustNotRun()
    response = app.test_client().post(
        "/api/accounts",
        json={"name": "账号", "username": "user", "password": "password"},
    )
    assert response.status_code == 201
    assert response.get_json()["data"]["verification_status"] == "unverified"
