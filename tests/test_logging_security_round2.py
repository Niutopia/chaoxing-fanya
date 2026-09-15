"""RED coverage for the second logging-security hardening pass."""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest

import main
from api.logger import (
    HISTORICAL_SENSITIVE_LOG,
    _sanitize_record,
    logger,
    sanitize_log_message,
)
from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences
from webapp.store import SQLiteStore
from webapp.task_logging import run_with_task_context
from webapp.task_manager import TaskManager


def test_sanitizer_covers_headers_urls_and_keeps_token_bucket_words():
    value = (
        "token: token-colon-secret token = token-equals-secret "
        "Authorization: Bearer authorization-secret "
        "Authorization Bearer authorization-space-secret "
        "Cookie: sid=cookie-one; theme=cookie-two "
        "Set-Cookie: sid=set-cookie-one; Path=/; HttpOnly "
        '{"Authorization": "Bearer json-secret", "Cookie": "sid=json-cookie"} '
        "https://user:url-secret@example.invalid/path?dtoken=url-secret#fragment "
        "token bucket progress"
    )

    cleaned = sanitize_log_message(
        value,
        secrets=(
            "token-colon-secret",
            "token-equals-secret",
            "authorization-secret",
            "authorization-space-secret",
            "cookie-one",
            "cookie-two",
            "set-cookie-one",
            "json-secret",
            "json-cookie",
            "url-secret",
        ),
    )

    assert "token bucket progress" in cleaned
    for secret in (
        "token-colon-secret",
        "token-equals-secret",
        "authorization-secret",
        "authorization-space-secret",
        "cookie-one",
        "cookie-two",
        "set-cookie-one",
        "json-secret",
        "json-cookie",
        "url-secret",
        "https://user:url-secret@example.invalid/path?dtoken=url-secret#fragment",
    ):
        assert secret not in cleaned
    assert "[redacted-url]" in cleaned


def test_sanitizer_long_punctuation_input_is_bounded():
    value = ("." * 120_000) + ("-" * 120_000)
    started = time.perf_counter()
    cleaned = sanitize_log_message(value)
    elapsed = time.perf_counter() - started

    assert len(cleaned) <= 20_000
    assert elapsed < 1.0


def test_record_extra_is_bounded_and_task_id_is_preserved():
    secret = "extra-secret-never-return"
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    cycle["bytes"] = secret.encode()

    record = {
        "message": f"safe token: {secret}",
        "extra": {
            "task_id": "task-token-123",
            "nested": cycle,
            "secret_token": secret,
            "object": SimpleNamespace(value=secret),
        },
        "exception": RuntimeError(secret),
    }

    _sanitize_record(record)

    assert record["extra"]["task_id"] == "task-token-123"
    assert secret not in record["message"]
    assert record["extra"]["secret_token"] == "[redacted]"
    assert record["extra"]["nested"]["bytes"] == "[redacted-bytes]"
    assert record["extra"]["nested"]["self"] == "[redacted-cycle]"
    assert record["extra"]["object"] == "[redacted-object]"
    assert record["exception"] is None


def test_task_context_rejects_untyped_or_oversized_ids_and_keeps_original():
    with pytest.raises(TypeError):
        run_with_task_context(123, lambda: None)
    with pytest.raises(ValueError):
        run_with_task_context("x" * 129, lambda: None)

    seen: list[str] = []
    sink_id = logger.add(
        lambda message: seen.append(message.record["extra"].get("task_id")),
        enqueue=False,
    )
    try:
        run_with_task_context("task-token-123", lambda: logger.info("progress"))
    finally:
        logger.remove(sink_id)
    assert "task-token-123" in seen


def test_worker_context_propagates_scoped_secret_into_each_executor_job(monkeypatch):
    class Engine:
        rate_limiter = SimpleNamespace(limit_rate=lambda **_: None)

        def get_job_list(self, *_args):
            return ([{"type": "document", "jobid": "a"}], {})

    def fake_process_job(*_args, config=None, **_kwargs):
        logger.info("token: {}", config["_log_secrets"][0])
        return SimpleNamespace(is_failure=lambda: False)

    monkeypatch.setattr(main, "process_job", fake_process_job)
    seen: list[str] = []
    sink_id = logger.add(lambda message: seen.append(message.record["message"]), enqueue=False)
    try:
        main.process_chapter(
            Engine(),
            {"courseId": "course", "title": "title"},
            {"id": "chapter", "title": "chapter", "has_finished": False},
            1.0,
            {
                "task_id": "task-token-123",
                "_log_secrets": ("worker-secret",),
                "jobs": 1,
                "notopen_action": "continue",
            },
        )
    finally:
        logger.remove(sink_id)

    assert seen
    assert all("worker-secret" not in message for message in seen)


def test_job_processor_threads_copy_scoped_task_context(monkeypatch):
    secret = "opaque-credential-value"

    def fake_process_chapter(*_args, **_kwargs):
        logger.info("worker scoped value {}", secret)
        return main.ChapterResult.SUCCESS

    monkeypatch.setattr(main, "process_chapter", fake_process_chapter)
    config = {
        "speed": 1.0,
        "jobs": 1,
        "notopen_action": "continue",
        "task_id": "job-context-task",
    }
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"title": "course"},
        [main.ChapterTask(index=0, point={"title": "chapter"})],
        config,
    )
    seen: list[str] = []
    sink_id = logger.add(
        lambda message: seen.append(message.record["message"]),
        enqueue=False,
    )
    try:
        run_with_task_context(
            "job-context-task",
            processor.run,
            log_secrets=(secret,),
        )
    finally:
        logger.remove(sink_id)

    assert seen
    assert all(secret not in message for message in seen)


def test_load_web_tasks_returns_clean_history_without_writing(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("account", "user", "password")
    store.save_web_task(
        {"id": "task-1", "account_id": account.id, "state": "completed"},
        {},
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "INSERT INTO web_task_logs(task_id, sequence, level, message, timestamp) "
            "VALUES (?, 1, 'INVALID', ?, 1)",
            ("task-1", "unrecognised old free text"),
        )
        connection.commit()

    loaded = store.load_web_tasks(limit=10)
    assert loaded[0]["logs"][0]["message"] == HISTORICAL_SENSITIVE_LOG
    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT level, message FROM web_task_logs WHERE task_id = 'task-1'"
        ).fetchone()
    assert row == ("INVALID", "unrecognised old free text")


def test_manager_owns_best_effort_sqlite_historical_writeback(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("account", "user", "password")
    store.save_web_task(
        {"id": "task-migrate", "account_id": account.id, "state": "completed"},
        {},
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "INSERT INTO web_task_logs(task_id, sequence, level, message, timestamp) "
            "VALUES (?, 1, 'INVALID', ?, 1)",
            ("task-migrate", "unrecognised legacy question text"),
        )
        connection.commit()

    manager = TaskManager(persistence=store)

    assert manager.get_logs("task-migrate").items[0].message == HISTORICAL_SENSITIVE_LOG
    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT level, message FROM web_task_logs WHERE task_id = 'task-migrate'"
        ).fetchone()
    assert row == ("info", HISTORICAL_SENSITIVE_LOG)


class _DirtyPersistence:
    def __init__(self):
        self.saved_logs: list[dict] = []

    def load_web_tasks(self, *, limit):
        return [
            {
                "snapshot": {
                    "id": "task-restore",
                    "account_id": "account",
                    "state": "completed",
                },
                "details": {},
                "logs": [
                    {
                        "sequence": 1,
                        "level": "info",
                        "message": "unknown old text",
                        "timestamp": 1,
                    }
                ],
            }
        ]

    def save_web_task_log(self, task_id, entry, *, capacity):
        self.saved_logs.append(dict(entry))
        return False


def test_failed_historical_migration_keeps_logs_dirty():
    persistence = _DirtyPersistence()
    manager = TaskManager(persistence=persistence)
    record = manager._tasks["task-restore"]

    assert record.logs_dirty is True
    assert persistence.saved_logs


def test_historical_migration_clears_dirty_only_after_retry_succeeds():
    class RetryPersistence(_DirtyPersistence):
        def __init__(self):
            super().__init__()
            self.allow_save = False

        def save_web_task_log(self, task_id, entry, *, capacity):
            self.saved_logs.append(dict(entry))
            return self.allow_save

    persistence = RetryPersistence()
    manager = TaskManager(persistence=persistence)
    record = manager._tasks["task-restore"]
    assert record.logs_dirty is True

    persistence.allow_save = True
    page = manager.get_logs(record.snapshot.id)

    assert page.items[0].message == HISTORICAL_SENSITIVE_LOG
    assert record.logs_dirty is False


def test_task_details_cycle_and_arbitrary_values_are_safe():
    manager = TaskManager(runner=lambda _context: None)
    task = manager.start(
        account_id="details-account",
        course_ids=["course"],
        preferences=AccountPreferences(
            selected_course_ids=["course"],
            speed=1.0,
            jobs=1,
            notopen_action="continue",
            answer_enabled=False,
            answer_cover_rate=0.9,
            answer_auto_submit=False,
            notification_config={},
            ocr_config={},
        ),
        auth=AccountAuth(username="u", password="", cookies={}),
    )
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    cycle["raw"] = object()
    manager.get_reporter(task.id).set_courses([cycle])

    details = manager.get_details(task.id)
    assert details.courses[0]["self"] == "[redacted-cycle]"
    assert details.courses[0]["raw"] == "[redacted-object]"


def test_worker_error_uses_conservative_historical_boundary():
    def fail(_context):
        raise RuntimeError("headers: legacy question body")

    manager = TaskManager(runner=fail)
    task = manager.start(
        account_id="error-account",
        course_ids=["course"],
        preferences=AccountPreferences(selected_course_ids=["course"]),
        auth=AccountAuth(username="u", password="", cookies={}),
    )
    assert manager.wait(task.id, timeout=1)

    assert manager.get_snapshot(task.id).error == HISTORICAL_SENSITIVE_LOG
