"""Round-seven regressions for trusted restore hints and task ordering."""

from __future__ import annotations

import sqlite3

from webapp.crypto import SecretBox
from webapp.store import SQLiteStore
from webapp.task_manager import TaskManager


def test_store_migrates_legacy_web_tasks_before_creating_history_index(tmp_path):
    data_dir = tmp_path / "data"
    database_path = data_dir / "db.sqlite3"
    data_dir.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE web_tasks (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                details_json TEXT NOT NULL,
                started_at REAL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()

    SQLiteStore(database_path, SecretBox(data_dir))

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(web_tasks)")
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(web_tasks)")
        }
    assert {"finished_at", "history_at"}.issubset(columns)
    assert "web_tasks_history_at_idx" in indexes


def test_custom_adapter_cannot_claim_ordered_logs_with_payload_hint():
    class Persistence:
        def load_web_tasks(self, *, limit):
            return [
                {
                    "snapshot": {
                        "id": "round7-forged-log-order",
                        "account_id": "round7-account",
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
                        for sequence in (100, 1, 2)
                    ],
                    # A normal payload field is not a trusted capability.
                    "_logs_ordered": True,
                }
            ]

    manager = TaskManager(persistence=Persistence(), log_capacity=2)

    page = manager.get_logs("round7-forged-log-order")

    assert [entry.sequence for entry in page.items] == [2, 100]
    assert page.next_cursor == 100


def test_interrupted_restore_keeps_loader_and_manager_history_order(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round7", "user", "password")

    store.save_web_task(
        {
            "id": "round7-active-missing-finished",
            "account_id": account.id,
            "state": "running",
            "started_at": 10.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round7-active-with-finished",
            "account_id": account.id,
            "state": "stopping",
            "started_at": 1.0,
            "finished_at": 40.0,
        },
        {},
    )
    store.save_web_task(
        {
            "id": "round7-terminal",
            "account_id": account.id,
            "state": "completed",
            "started_at": 2.0,
            "finished_at": 30.0,
        },
        {},
    )

    loaded_ids = [
        row["snapshot"]["id"] for row in store.load_web_tasks(limit=10)
    ]

    manager = TaskManager(persistence=store, terminal_task_capacity=10)

    assert [item.id for item in manager.list_tasks()] == loaded_ids
    assert manager.get_snapshot("round7-active-missing-finished").finished_at is not None

    # The generated UI failure timestamp must not become the durable history
    # ordering key on a subsequent app rebuild.
    rebuilt = TaskManager(persistence=store, terminal_task_capacity=10)
    assert [item.id for item in rebuilt.list_tasks()] == loaded_ids


def test_loader_manager_and_pruner_use_finished_at_then_id_tie_break(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("round7-ties", "user", "password")

    for task_id in (
        "round7-terminal-a",
        "round7-terminal-b",
        "round7-terminal-c",
    ):
        store.save_web_task(
            {
                "id": task_id,
                "account_id": account.id,
                "state": "failed",
                "started_at": 1.0,
                "finished_at": 30.0,
            },
            {},
        )

    loaded_ids = [
        row["snapshot"]["id"] for row in store.load_web_tasks(limit=2)
    ]
    assert loaded_ids == ["round7-terminal-c", "round7-terminal-b"]

    manager = TaskManager(persistence=store, terminal_task_capacity=2)

    assert [item.id for item in manager.list_tasks()] == loaded_ids

    with store._connection() as connection:
        persisted_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM web_tasks ORDER BY id"
            )
        ]
    assert set(persisted_ids) == set(loaded_ids)
