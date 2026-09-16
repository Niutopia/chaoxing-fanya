"""Round-five regression coverage for task persistence boundaries."""

from __future__ import annotations

import json
import sqlite3

from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences
from webapp.store import SQLiteStore
from webapp.task_manager import TaskManager, _secret_values


def _inputs(account_id: str) -> dict:
    return {
        "account_id": account_id,
        "course_ids": ["course-1"],
        "preferences": AccountPreferences(selected_course_ids=["course-1"]),
        "auth": AccountAuth(username="user", password="", cookies={}),
    }


def test_secret_collector_scans_mapping_after_list_limit_without_overrunning_nodes():
    config = {f"safe-{index}": index for index in range(256)}
    config["late-provider"] = {"api_key": "late-provider-key"}

    values = _secret_values(
        AccountAuth(username="user", password="", cookies={}),
        None,
        ocr_config=config,
        notification_config={},
    )

    assert "late-provider-key" in values


def test_public_append_log_cannot_replay_a_preterminal_event_after_cutoff():
    manager = TaskManager(runner=lambda _context: None, max_active_accounts=1)
    task = manager.start(**_inputs("event-time-account"))
    assert manager.wait(task.id, timeout=1)

    cutoff = manager._tasks[task.id].terminal_cutoff
    assert cutoff is not None
    assert manager.append_log(
        task.id,
        "forged-old-event",
        _event_time=cutoff - 1,
    ) is None
    assert manager.append_log(
        task.id,
        "forged-nan-event",
        _event_time=float("nan"),
    ) is None
    assert manager.get_logs(task.id).items == []


def test_store_marks_legacy_snapshot_details_dirty_and_manager_writes_clean_values_back(
    tmp_path,
):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("account", "user", "password")
    task_id = "round5-legacy-record"
    secret = "legacy-record-secret"
    store.save_web_task(
        {"id": task_id, "account_id": account.id, "state": "completed"},
        {},
    )
    raw_snapshot = {
        "id": task_id,
        "account_id": account.id,
        "state": "completed",
        "stats": {"completed": 2, "password": secret},
    }
    raw_details = {
        "courses": [{"title": "Course", "api_key": secret}],
        "counts": {"completed": 2},
    }
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE web_tasks SET snapshot_json = ?, details_json = ? WHERE id = ?",
            (json.dumps(raw_snapshot), json.dumps(raw_details), task_id),
        )
        connection.commit()

    # The loader must be read-only: it returns a dirty marker but does not
    # overwrite the original legacy value itself.
    loaded = store.load_web_tasks(limit=10)
    assert loaded[0]["_record_dirty"] is True
    assert loaded[0]["snapshot"]["stats"]["password"] == "[redacted]"
    with sqlite3.connect(store.db_path) as connection:
        before = connection.execute(
            "SELECT snapshot_json, details_json FROM web_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert secret in before[0] and secret in before[1]

    manager = TaskManager(persistence=store)
    assert manager.get_snapshot(task_id).stats["password"] == "[redacted]"
    with sqlite3.connect(store.db_path) as connection:
        after = connection.execute(
            "SELECT snapshot_json, details_json FROM web_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert secret not in after[0] and secret not in after[1]


def test_failed_legacy_record_writeback_stays_dirty_until_a_later_retry():
    secret = "retry-record-secret"

    class Persistence:
        def __init__(self):
            self.allow = False
            self.saved = []

        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round5-dirty-record",
                        "account_id": "round5-account",
                        "state": "completed",
                        "stats": {"password": secret},
                    },
                    "details": {"counts": {"api_key": secret}},
                    "logs": [],
                    "_record_dirty": True,
                }
            ]

        def save_web_task(self, snapshot, details):
            self.saved.append((snapshot, details))
            return self.allow

    persistence = Persistence()
    manager = TaskManager(persistence=persistence)
    record = manager._tasks["round5-dirty-record"]
    assert record.record_dirty is True
    assert persistence.saved
    assert secret not in repr(persistence.saved[-1])

    persistence.allow = True
    assert manager.get_snapshot(record.snapshot.id).stats["password"] == "[redacted]"
    assert record.record_dirty is False


def test_terminal_retention_uses_finished_at_for_capacity_and_matches_database(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("account", "user", "password")
    store.save_web_task(
        {
            "id": "round5-finished-first",
            "account_id": account.id,
            "state": "completed",
            "started_at": 1.0,
            "finished_at": 100.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round5-started-first",
            "account_id": account.id,
            "state": "completed",
            "started_at": 2.0,
            "finished_at": 50.0,
        },
        {},
    )

    manager = TaskManager(persistence=store, terminal_task_capacity=1)

    assert [item.id for item in manager.list_tasks()] == ["round5-finished-first"]
    with sqlite3.connect(store.db_path) as connection:
        persisted = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM web_tasks WHERE id LIKE 'round5-%'"
            )
        ]
    assert persisted == ["round5-finished-first"]


def test_loader_restores_all_active_rows_before_terminal_limit_and_normalizes_them(
    tmp_path,
):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("account", "user", "password")
    active_ids = ["round5-running-old", "round5-stopping-new"]
    for index, task_id in enumerate(active_ids):
        store.save_web_task(
            {
                "id": task_id,
                "account_id": account.id,
                "state": "running" if index == 0 else "stopping",
                "started_at": float(index + 1),
            },
            {},
        )
    terminal_ids = []
    for index in range(3):
        task_id = f"round5-terminal-{index}"
        terminal_ids.append(task_id)
        store.save_web_task(
            {
                "id": task_id,
                "account_id": account.id,
                "state": "completed",
                "started_at": float(index + 10),
                "finished_at": float(index + 10),
            },
            {},
        )

    loaded = store.load_web_tasks(limit=1)
    assert {row["snapshot"]["id"] for row in loaded} == set(active_ids + terminal_ids[-1:])

    manager = TaskManager(persistence=store, terminal_task_capacity=1)
    assert {item.id for item in manager.list_tasks()} >= set(active_ids)
    assert all(
        manager.get_snapshot(task_id).state == "failed" for task_id in active_ids
    )
    with sqlite3.connect(store.db_path) as connection:
        states = dict(
            connection.execute(
                "SELECT id, json_extract(snapshot_json, '$.state') "
                "FROM web_tasks WHERE id LIKE 'round5-%'"
            ).fetchall()
        )
    assert all(states[task_id] == "failed" for task_id in active_ids)


def test_custom_log_restore_is_bounded_and_list_retains_latest_capacity():
    class GuardedList(list):
        def __iter__(self):
            for index, item in enumerate(super().__iter__()):
                if index >= 100:
                    raise RuntimeError("unbounded list traversal")
                yield item

    class Persistence:
        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round5-log-budget",
                        "account_id": "round5-account",
                        "state": "completed",
                    },
                    "details": {},
                    "logs": GuardedList([
                        {
                            "sequence": index,
                            "level": "info",
                            "message": f"entry-{index}",
                            "timestamp": float(index),
                        }
                        for index in range(1, 10_001)
                    ]),
                }
            ]

    manager = TaskManager(persistence=Persistence(), log_capacity=3)
    page = manager.get_logs("round5-log-budget")
    assert [entry.sequence for entry in page.items] == [9_998, 9_999, 10_000]
    assert page.next_cursor == 10_000

    class BrokenIterable:
        def __iter__(self):
            for index in range(10_000):
                if index >= 100:
                    raise RuntimeError("unbounded iterable")
                yield {
                    "sequence": index + 1,
                    "level": "info",
                    "message": "safe",
                    "timestamp": float(index + 1),
                }

    class BrokenPersistence:
        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round5-broken-iterable",
                        "account_id": "round5-account",
                        "state": "completed",
                    },
                    "details": {},
                    "logs": BrokenIterable(),
                }
            ]

    manager = TaskManager(persistence=BrokenPersistence(), log_capacity=3)
    assert manager.get_logs("round5-broken-iterable").next_cursor <= 100
