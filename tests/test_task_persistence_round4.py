"""Focused regression coverage for the round-four task boundaries.

These tests exercise only the process-local task monitor and SQLite adapter.
They do not contact Chaoxing, the answer service, or any external endpoint.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from api.logger import logger
from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences
from webapp.store import SQLiteStore
from webapp.task_logging import (
    _SINK_LOCK,
    install_task_log_sink,
    remove_task_log_sink,
    run_with_task_context,
)
from webapp.task_manager import TaskManager
from webapp.task_manager import _secret_values


PASSWORD = "round4-password-never-persisted"
COOKIE = "round4-cookie-never-persisted"
API_KEY = "round4-api-key-never-persisted"


def _task_inputs(account_id: str = "round4-account") -> dict:
    return {
        "account_id": account_id,
        "course_ids": ["course-1"],
        "preferences": AccountPreferences(selected_course_ids=["course-1"]),
        "auth": AccountAuth(
            username="round4-user",
            password=PASSWORD,
            cookies={"sid": COOKIE},
        ),
    }


def test_enqueue_final_log_is_kept_when_route_runs_after_terminal_transition(monkeypatch):
    """A queued record created before return must survive a late sink callback."""

    entered = threading.Event()
    release = threading.Event()

    def runner(_context):
        logger.info("checkpoint-final-1")

    import webapp.task_logging as task_logging

    original_route = task_logging._route_record
    def delayed_route(message):
        entered.set()
        assert release.wait(timeout=2)
        original_route(message)

    monkeypatch.setattr(task_logging, "_route_record", delayed_route)
    manager = TaskManager(runner=runner, max_active_accounts=1)
    sink_id = install_task_log_sink(manager)
    try:
        task = manager.start(**_task_inputs())
        assert entered.wait(timeout=1)
        # The worker is allowed to publish its terminal state while the
        # process-wide enqueue worker is still blocked in the test gate.
        assert manager._tasks[task.id].done.wait(timeout=1)
        release.set()
        logger.complete()
        assert [item.message for item in manager.get_logs(task.id).items] == [
            "checkpoint-final-1"
        ]
    finally:
        release.set()
        remove_task_log_sink(sink_id)


def test_enqueue_log_created_after_terminal_cutoff_is_rejected(monkeypatch):
    """A genuinely post-terminal record must not be admitted by the late drain."""

    entered = threading.Event()
    release = threading.Event()

    def runner(_context):
        logger.info("progress 1")

    import webapp.task_logging as task_logging

    original_route = task_logging._route_record
    # Keep the worker from waiting on the deliberately blocked queue while
    # this test emits the genuinely post-terminal record.
    monkeypatch.setattr(task_logging, "complete_task_log_sink", lambda: None)

    def delayed_route(message):
        entered.set()
        assert release.wait(timeout=2)
        original_route(message)

    monkeypatch.setattr(task_logging, "_route_record", delayed_route)
    manager = TaskManager(runner=runner, max_active_accounts=1)
    sink_id = install_task_log_sink(manager)
    try:
        task = manager.start(**_task_inputs("post-terminal-account"))
        assert entered.wait(timeout=1)
        assert manager._tasks[task.id].done.wait(timeout=1)
        run_with_task_context(task.id, lambda: logger.info("progress 2"))
        release.set()
        logger.complete()
        messages = [item.message for item in manager.get_logs(task.id).items]
        assert messages == ["progress 1"]
    finally:
        release.set()
        remove_task_log_sink(sink_id)


def test_enqueue_sink_can_be_completed_while_routing_lock_is_held():
    """The lock/queue protocol must not deadlock a synchronous Loguru drain."""

    manager = TaskManager(runner=lambda _context: None, max_active_accounts=1)
    sink_id = install_task_log_sink(manager)
    try:
        task = manager.start(**_task_inputs("lock-account"))
        assert manager.wait(task.id, timeout=1)

        completed = threading.Event()

        def complete_under_lock():
            with _SINK_LOCK:
                logger.info("lock-drain-after-terminal")
                logger.complete()
            completed.set()

        thread = threading.Thread(target=complete_under_lock, daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert completed.is_set()
    finally:
        remove_task_log_sink(sink_id)


def test_one_hundred_fast_tasks_keep_their_final_logs_after_enqueue_drain(monkeypatch):
    """High-turnover workers must not lose queued terminal-adjacent logs."""

    expected = 100
    entered = threading.Event()
    release = threading.Event()
    count_lock = threading.Lock()
    entered_count = 0

    def runner(context):
        logger.info("checkpoint-{}", context.account_id)

    import webapp.task_logging as task_logging

    original_route = task_logging._route_record

    def delayed_route(message):
        nonlocal entered_count
        with count_lock:
            entered_count += 1
            if entered_count == 1:
                entered.set()
        if entered_count == 1:
            assert release.wait(timeout=5)
        original_route(message)

    monkeypatch.setattr(task_logging, "_route_record", delayed_route)
    # Use one manager per task so the test exercises the process-wide sink's
    # turnover without making the manager's ten-account admission limit part
    # of the assertion.
    managers = [
        TaskManager(
            runner=runner,
            max_active_accounts=1,
            terminal_task_capacity=expected,
        )
        for _ in range(expected)
    ]
    sink_id = install_task_log_sink(managers[0])
    for manager in managers[1:]:
        install_task_log_sink(manager)
    task_ids: list[str] = []
    try:
        for index, manager in enumerate(managers):
            task = manager.start(**_task_inputs(f"fast-{index}"))
            task_ids.append(task.id)
        assert entered.wait(timeout=5)
        release.set()
        for manager, task_id in zip(managers, task_ids):
            assert manager.wait(task_id, timeout=2)
        logger.complete()
        for account_id, task_id in zip(
            (f"fast-{index}" for index in range(expected)), task_ids
        ):
            current_manager = managers[int(account_id.removeprefix("fast-"))]
            assert [item.message for item in current_manager.get_logs(task_id).items] == [
                f"checkpoint-{account_id}"
            ]
    finally:
        release.set()
        remove_task_log_sink(sink_id)


def test_store_redacts_task_snapshot_and_details_at_the_persistence_boundary(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round4", "user", PASSWORD)
    snapshot = {
        "id": "round4-store-task",
        "account_id": account.id,
        "state": "completed",
        "progress": 3,
        "total": 4,
        "current_course": {"title": "Ordinary course", "password": PASSWORD},
        "current_chapter": {"title": "Ordinary chapter", "cookie": COOKIE},
        "current_task": {"title": "Ordinary task", "api_key": API_KEY},
        "stats": {"completed": 3, "api_key": API_KEY},
    }
    details = {
        "courses": [
            {"id": "course-1", "title": "Ordinary course", "password": PASSWORD}
        ],
        "active_jobs": {"job-1": {"title": "Ordinary job", "cookie": COOKIE}},
        "counts": {"completed": 3, "api_key": API_KEY},
    }

    assert store.save_web_task(snapshot, details) is True
    with sqlite3.connect(store.db_path) as connection:
        raw = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT snapshot_json FROM web_tasks WHERE id = ? UNION ALL "
                "SELECT details_json FROM web_tasks WHERE id = ?",
                (snapshot["id"], snapshot["id"]),
            )
        )
    for secret in (PASSWORD, COOKIE, API_KEY):
        assert secret not in raw

    loaded = store.load_web_tasks(limit=10)
    assert loaded[0]["snapshot"]["progress"] == 3
    assert loaded[0]["snapshot"]["stats"]["completed"] == 3
    assert loaded[0]["details"]["courses"][0]["title"] == "Ordinary course"
    serialized = json.dumps(loaded, ensure_ascii=False, sort_keys=True)
    for secret in (PASSWORD, COOKIE, API_KEY):
        assert secret not in serialized

    # Re-saving the already-clean shape must be idempotent.
    assert store.save_web_task(loaded[0]["snapshot"], loaded[0]["details"]) is True
    assert store.load_web_tasks(limit=10) == loaded


def test_manager_restore_redacts_custom_persistence_payload_and_bounds_cycles():
    secret = "restore-api-key-secret"
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    cycle["safe_count"] = 7

    class Persistence:
        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round4-restore-task",
                        "account_id": "round4-restore-account",
                        "state": "completed",
                        "current_course": {"title": "Course", "password": secret},
                        "stats": {"completed": 7, "api_key": secret, "cycle": cycle},
                    },
                    "details": {
                        "courses": [{"title": "Course", "cookie": secret}],
                        "active_jobs": {"job": {"api_key": secret}},
                        "counts": {"completed": 7, "cycle": cycle},
                    },
                    "logs": [],
                }
            ]

    manager = TaskManager(persistence=Persistence())
    snapshot = manager.get_snapshot("round4-restore-task")
    details = manager.get_details("round4-restore-task")
    assert snapshot.stats["completed"] == 7
    assert snapshot.stats["api_key"] == "[redacted]"
    assert snapshot.stats["cycle"]["self"] == "[redacted-cycle]"
    assert details.courses[0]["title"] == "Course"
    assert details.courses[0]["cookie"] == "[redacted]"
    assert details.active_jobs["job"]["api_key"] == "[redacted]"
    assert secret not in repr(snapshot)
    assert secret not in repr(details)


def test_secret_collector_handles_mapping_list_cycles_and_nested_aliases():
    shared = {"push_key": "shared-push-key"}
    cycle: dict[str, object] = {"self": None}
    cycle["self"] = cycle
    config = {
        "providers": [
            {"token": "nested-token", "aliases": [shared]},
            shared,
            cycle,
        ],
        "ocr": {"api_key": "nested-api-key"},
    }
    values = _secret_values(
        AccountAuth(username="u", password="", cookies={}),
        None,
        ocr_config=config,
        notification_config={},
    )
    assert {"nested-token", "shared-push-key", "nested-api-key"}.issubset(values)


def test_loader_and_restart_prune_logs_and_terminal_tasks_to_configured_capacity(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round4", "user", PASSWORD)
    log_task_id = "round4-log-capacity-task"
    store.save_web_task(
        {"id": log_task_id, "account_id": account.id, "state": "completed"}, {}
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.executemany(
            "INSERT INTO web_task_logs(task_id, sequence, level, message, timestamp) "
            "VALUES (?, ?, 'info', ?, ?)",
            [(log_task_id, index, f"entry-{index}", float(index)) for index in range(1, 5002)],
        )
        connection.commit()

    loaded = store.load_web_tasks(limit=10)
    assert len(loaded[0]["logs"]) <= 5000

    manager = TaskManager(
        persistence=store,
        log_capacity=5000,
        terminal_task_capacity=2,
    )
    assert len(manager.get_logs(log_task_id).items) == 5000
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM web_task_logs WHERE task_id = ?", (log_task_id,)
        ).fetchone()[0] == 5000

    terminal_ids = []
    for index in range(3):
        task_id = f"round4-terminal-{index}"
        terminal_ids.append(task_id)
        store.save_web_task(
            {
                "id": task_id,
                "account_id": account.id,
                "state": "completed",
                "started_at": float(index + 1),
            },
            {},
        )

    TaskManager(
        persistence=store,
        log_capacity=5000,
        terminal_task_capacity=2,
    )
    with sqlite3.connect(store.db_path) as connection:
        persisted_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM web_tasks WHERE id LIKE 'round4-terminal-%' "
                "ORDER BY started_at DESC, id DESC"
            )
        ]
    assert persisted_ids == terminal_ids[-2:][::-1]


def test_failed_persistence_return_value_keeps_log_dirty_for_retry():
    class Persistence:
        def __init__(self):
            self.allow = False

        def save_web_task(self, _snapshot, _details):
            return True

        def save_web_task_log(self, _task_id, _entry, *, capacity):
            return self.allow

    release = threading.Event()
    persistence = Persistence()
    manager = TaskManager(
        runner=lambda _context: release.wait(timeout=2),
        persistence=persistence,
        max_active_accounts=1,
    )
    task = manager.start(**_task_inputs("dirty-account"))
    try:
        assert manager.append_log(task.id, "retry-1") is not None
        assert manager._tasks[task.id].logs_dirty is True
        persistence.allow = True
        assert [item.message for item in manager.get_logs(task.id).items] == ["retry-1"]
        assert manager._tasks[task.id].logs_dirty is False
    finally:
        release.set()
        manager.wait(task.id, timeout=1)
