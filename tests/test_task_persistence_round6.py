"""Round-six regressions for bounded task persistence restoration."""

from __future__ import annotations

import json
import sqlite3

from api.logger import HISTORICAL_SENSITIVE_LOG
from webapp.crypto import SecretBox
from webapp.store import SQLiteStore
from webapp.task_manager import TaskManager


def test_loader_globally_orders_selected_active_and_terminal_rows(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round6", "user", "password")

    store.save_web_task(
        {
            "id": "round6-active-old",
            "account_id": account.id,
            "state": "running",
            "started_at": 10.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round6-active-new",
            "account_id": account.id,
            "state": "stopping",
            "started_at": 20.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round6-terminal-new",
            "account_id": account.id,
            "state": "completed",
            "started_at": 1.0,
            "finished_at": 30.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round6-terminal-old",
            "account_id": account.id,
            "state": "completed",
            "started_at": 2.0,
            "finished_at": 25.0,
        },
        {},
    )

    loaded = store.load_web_tasks(limit=1)

    assert [row["snapshot"]["id"] for row in loaded] == [
        "round6-terminal-new",
        "round6-active-new",
        "round6-active-old",
    ]


def test_custom_unordered_log_list_selects_highest_sequences_within_capacity():
    class Persistence:
        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round6-unsorted-logs",
                        "account_id": "round6-account",
                        "state": "completed",
                    },
                    "details": {},
                    "logs": [
                        {
                            "sequence": sequence,
                            "level": "info",
                            "message": f"entry-{sequence}",
                            "timestamp": float(sequence),
                        }
                        for sequence in (1, 100, 2, 3)
                    ],
                }
            ]

    manager = TaskManager(persistence=Persistence(), log_capacity=2)

    page = manager.get_logs("round6-unsorted-logs")

    assert [entry.sequence for entry in page.items] == [3, 100]
    assert page.next_cursor == 100


def test_legacy_terminal_error_cleaning_marks_dirty_and_retries_writeback():
    class Persistence:
        def __init__(self):
            self.allow = False
            self.saved = []

        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round6-legacy-error",
                        "account_id": "round6-account",
                        "state": "failed",
                        "error": "free sensitive legacy error",
                    },
                    "details": {},
                    "logs": [],
                }
            ]

        def save_web_task(self, snapshot, details):
            self.saved.append((snapshot, details))
            return self.allow

    persistence = Persistence()
    manager = TaskManager(persistence=persistence)
    record = manager._tasks["round6-legacy-error"]

    assert record.snapshot.error == "历史敏感日志已清理"
    assert record.record_dirty is True
    assert persistence.saved[-1][0]["error"] == "历史敏感日志已清理"

    persistence.allow = True
    assert manager.get_snapshot(record.snapshot.id).error == "历史敏感日志已清理"
    assert record.record_dirty is False


def test_sqlite_loader_marks_legacy_terminal_error_dirty_without_writing(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round6", "user", "password")
    task_id = "round6-sqlite-legacy-error"
    store.save_web_task(
        {"id": task_id, "account_id": account.id, "state": "failed"},
        {},
    )
    raw_snapshot = {
        "id": task_id,
        "account_id": account.id,
        "state": "failed",
        "error": "free sensitive legacy error",
    }
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE web_tasks SET snapshot_json = ? WHERE id = ?",
            (json.dumps(raw_snapshot), task_id),
        )
        connection.commit()

    loaded = store.load_web_tasks(limit=1)

    assert loaded[0]["snapshot"]["error"] == HISTORICAL_SENSITIVE_LOG
    assert loaded[0]["_record_dirty"] is True
    with sqlite3.connect(store.db_path) as connection:
        assert "free sensitive legacy error" in connection.execute(
            "SELECT snapshot_json FROM web_tasks WHERE id = ?", (task_id,)
        ).fetchone()[0]
