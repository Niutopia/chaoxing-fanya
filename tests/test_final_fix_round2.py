"""Round 2 regression tests for malformed boundaries and account state safety."""

from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from flask import abort

from webapp import create_app
from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences
from webapp.study_runner import ChaoxingStudyRunner, StudyRunError
from webapp.store import SQLiteStore


@pytest.fixture
def store(app):
    return app.extensions["services"]["store"]


class _ResponseEngine:
    rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)

    def __init__(self, common_config):
        self.common_config = common_config

    def login(self, login_with_cookies=False):
        return {"status": True}

    def get_course_list(self):
        return [{"courseId": "math", "title": "高等数学", "clazzId": "c", "cpi": "p"}]


class _KnownEmptyPointsEngine(_ResponseEngine):
    def get_course_point(self, *_args):
        return {"points": []}


class _MalformedPointsEngine(_ResponseEngine):
    def get_course_point(self, *_args):
        return {"unexpected": []}


class _KnownEmptyJobsEngine(_ResponseEngine):
    def get_course_point(self, *_args):
        return {"points": [{"id": "chapter", "title": "第一章", "has_finished": False}]}

    def get_job_list(self, *_args):
        return {"data": {"jobs": [], "notOpen": False}}


class _MalformedJobsEngine(_KnownEmptyJobsEngine):
    def get_job_list(self, *_args):
        return {"unexpected": []}


def _runner_context(course_ids=None):
    course_ids = course_ids or ["math"]
    return SimpleNamespace(
        task_id="round2-task",
        account_id="round2-account",
        course_ids=course_ids,
        preferences=AccountPreferences(selected_course_ids=course_ids),
        auth=AccountAuth(username="user", password="password", cookies={}),
        answer=None,
        answer_semaphore=None,
        cancel_event=threading.Event(),
        reporter=SimpleNamespace(),
    )


def test_known_empty_engine_response_is_a_successful_empty_run(tmp_path):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=_KnownEmptyPointsEngine)

    runner.run(_runner_context())


def test_malformed_course_points_response_fails_instead_of_completing(tmp_path):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=_MalformedPointsEngine)

    with pytest.raises(StudyRunError) as captured:
        runner.run(_runner_context())

    assert captured.value.code == "engine_response_invalid"


def test_known_empty_job_response_is_a_successful_empty_chapter(tmp_path):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=_KnownEmptyJobsEngine)

    runner.run(_runner_context())


def test_malformed_job_response_fails_instead_of_completing(tmp_path):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=_MalformedJobsEngine)

    with pytest.raises(StudyRunError) as captured:
        runner.run(_runner_context())

    assert captured.value.code == "engine_response_invalid"


def test_legacy_dual_secret_migration_clears_incompatible_secret_and_verification(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "chaoxing-web.sqlite3"
    secret_box = SecretBox(data_dir)
    password_token = secret_box.encrypt("legacy-password")
    cookies_token = secret_box.encrypt(json.dumps({"sid": "legacy-cookie"}))
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
        VALUES ('legacy-dual', '旧账号', 'legacy-user', ?, ?, 1, 'valid',
                '2026-09-14T00:00:00+00:00', 'now', 'now')
        """,
        (password_token, cookies_token),
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(database_path, secret_box)
    profile = store.get_account("legacy-dual")
    auth = store.get_account_auth("legacy-dual")

    assert profile.auth_mode == "password"
    assert auth.password == "legacy-password"
    assert auth.cookies == {}
    assert profile.has_cookies is False
    assert profile.verification_status == "unverified"
    assert profile.last_verified_at is None


def test_explicit_password_mode_cleanup_resets_valid_verification(store):
    profile = store.create_account(
        "账号", "user", "password", auth_mode="password", verification_status="unverified"
    )
    # Compatibility integrations can still add the legacy second secret.
    store.update_account(profile.id, cookies={"sid": "legacy-cookie"})
    store.update_account(
        profile.id,
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )

    updated = store.update_account(profile.id, auth_mode="password", password="password")
    auth = store.get_account_auth(profile.id)

    assert auth.cookies == {}
    assert updated.has_cookies is False
    assert updated.verification_status == "unverified"
    assert updated.last_verified_at is None


def test_explicit_cookie_mode_cleanup_resets_valid_verification(store):
    profile = store.create_account(
        "账号", "user", None, auth_mode="cookies", cookies={"sid": "cookie"}
    )
    # Compatibility integrations can still add the legacy second secret.
    store.update_account(profile.id, password="legacy-password")
    store.update_account(
        profile.id,
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )

    updated = store.update_account(profile.id, auth_mode="cookies", cookies={"sid": "cookie"})
    auth = store.get_account_auth(profile.id)

    assert auth.password == ""
    assert updated.has_secret is False
    assert updated.verification_status == "unverified"
    assert updated.last_verified_at is None


class _MalformedStateManager:
    lock = threading.RLock()

    def __init__(self, snapshots):
        self.snapshots = snapshots

    def list_tasks(self):
        return self.snapshots


@pytest.mark.parametrize(
    "snapshots",
    [
        [object()],
        {},
        [{"state": "unknown"}],
        [SimpleNamespace()],
    ],
)
def test_settings_malformed_task_state_fails_closed_without_mutation(tmp_path, snapshots):
    manager = _MalformedStateManager(snapshots)
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "TASK_MANAGER": manager})
    store = app.extensions["services"]["store"]
    before = store.get_answer_connection()

    response = app.test_client().put(
        "/api/settings/answer-connection",
        json={"base_url": "http://localhost:8849/v1"},
    )

    assert response.status_code == 503
    assert response.get_json()["code"] == "settings_state_unavailable"
    assert store.get_answer_connection() == before


def test_http_exception_status_and_semantics_survive_generic_handler(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})

    @app.get("/round2-abort")
    def round2_abort():
        abort(400, description="bad request")

    client = app.test_client()
    method_not_allowed = client.post("/")
    bad_request = client.get("/round2-abort")

    assert method_not_allowed.status_code == 405
    assert bad_request.status_code == 400
