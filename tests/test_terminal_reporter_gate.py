"""Regression tests for reporter updates that race task terminalization."""

from __future__ import annotations

import threading

from webapp.crypto import SecretBox
from webapp.models import AccountAuth, AccountPreferences
from webapp.store import SQLiteStore
from webapp.task_manager import TaskManager


class TerminalRunner:
    """Capture the reporter before returning or failing the owning task."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.reporter = None

    def run(self, context) -> None:
        self.reporter = context.reporter
        if self.outcome == "failed":
            raise RuntimeError("owner failure")


class BlockingRunner:
    """Keep the task in ``running``/``stopping`` while a test updates it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.reporter = None

    def run(self, context) -> None:
        self.reporter = context.reporter
        self.entered.set()
        self.release.wait(timeout=2)


class GatePassedBlockedRunner:
    """Enter a callback before the owner fails, then publish after terminalization."""

    def __init__(self) -> None:
        self.callback_entered = threading.Event()
        self.release_callback = threading.Event()
        self.callback_done = threading.Event()
        self.callback_errors: list[BaseException] = []
        self.callback_thread: threading.Thread | None = None

    def run(self, context) -> None:
        reporter = context.reporter

        def late_callback() -> None:
            # This models the callback having passed the outer worker gate
            # before its body was blocked on another thread/event.
            self.callback_entered.set()
            self.release_callback.wait(timeout=2)
            try:
                _publish_all_reporter_mutations(reporter)
            except BaseException as exc:
                self.callback_errors.append(exc)
            finally:
                self.callback_done.set()

        self.callback_thread = threading.Thread(
            target=late_callback,
            name="late-reporter-callback",
            daemon=True,
        )
        self.callback_thread.start()
        assert self.callback_entered.wait(timeout=1)
        raise RuntimeError("owner failure")


def _new_store(tmp_path):
    data_dir = tmp_path / "data"
    store = SQLiteStore(data_dir / "db.sqlite3", SecretBox(data_dir))
    account = store.create_account("Reporter gate", "reporter-user", "password")
    return store, account.id


def _task_inputs(account_id: str) -> dict:
    return {
        "account_id": account_id,
        "course_ids": ["course-1"],
        "preferences": AccountPreferences(selected_course_ids=["course-1"]),
        "auth": AccountAuth(username="reporter-user", password="password", cookies={}),
    }


def _publish_all_reporter_mutations(reporter) -> None:
    reporter.set_current(
        course="late-course",
        chapter="late-chapter",
        task="late-task",
    )
    reporter.set_counts(
        progress=99,
        total=100,
        completed_tasks=99,
    )
    reporter.set_courses([{"id": "late-course", "status": "running"}])
    reporter.set_active_jobs(
        {"late-job": {"id": "late-job", "progress": 99}}
    )


def test_completed_task_ignores_all_late_reporter_mutations_and_db_stays_unchanged(
    tmp_path,
):
    store, account_id = _new_store(tmp_path)
    runner = TerminalRunner("completed")
    manager = TaskManager(runner=runner, persistence=store)

    task = manager.start(**_task_inputs(account_id))
    assert manager.wait(task.id, timeout=1)
    before_snapshot = manager.get_snapshot(task.id)
    before_details = manager.get_details(task.id)
    before_db = store.load_web_tasks(limit=10)

    _publish_all_reporter_mutations(runner.reporter)

    assert manager.get_snapshot(task.id) == before_snapshot
    assert manager.get_details(task.id) == before_details
    assert store.load_web_tasks(limit=10) == before_db


def test_failed_task_ignores_all_late_reporter_mutations_and_db_stays_unchanged(
    tmp_path,
):
    store, account_id = _new_store(tmp_path)
    runner = TerminalRunner("failed")
    manager = TaskManager(runner=runner, persistence=store)

    task = manager.start(**_task_inputs(account_id))
    assert manager.wait(task.id, timeout=1)
    before_snapshot = manager.get_snapshot(task.id)
    before_details = manager.get_details(task.id)
    before_db = store.load_web_tasks(limit=10)

    _publish_all_reporter_mutations(runner.reporter)

    assert manager.get_snapshot(task.id) == before_snapshot
    assert manager.get_details(task.id) == before_details
    assert store.load_web_tasks(limit=10) == before_db


def test_callback_that_passed_outer_gate_cannot_mutate_after_owner_terminalizes(tmp_path):
    store, account_id = _new_store(tmp_path)
    runner = GatePassedBlockedRunner()
    manager = TaskManager(runner=runner, persistence=store)

    task = manager.start(**_task_inputs(account_id))
    assert runner.callback_entered.wait(timeout=1)
    assert manager.wait(task.id, timeout=1)
    before_snapshot = manager.get_snapshot(task.id)
    before_details = manager.get_details(task.id)
    before_db = store.load_web_tasks(limit=10)

    runner.release_callback.set()
    assert runner.callback_done.wait(timeout=1)
    assert runner.callback_thread is not None
    runner.callback_thread.join(timeout=1)

    assert runner.callback_errors == []
    assert manager.get_snapshot(task.id) == before_snapshot
    assert manager.get_details(task.id) == before_details
    assert store.load_web_tasks(limit=10) == before_db


def test_running_task_still_accepts_reporter_mutations(tmp_path):
    store, account_id = _new_store(tmp_path)
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, persistence=store)

    task = manager.start(**_task_inputs(account_id))
    try:
        assert runner.entered.wait(timeout=1)
        _publish_all_reporter_mutations(runner.reporter)

        snapshot = manager.get_snapshot(task.id)
        details = manager.get_details(task.id)
        assert snapshot.state == "running"
        assert snapshot.current_course == "late-course"
        assert snapshot.current_chapter == "late-chapter"
        assert snapshot.current_task == "late-task"
        assert snapshot.progress == 99
        assert snapshot.total == 100
        assert snapshot.stats == {
            "progress": 99,
            "total": 100,
            "completed_tasks": 99,
        }
        assert details.courses == [{"id": "late-course", "status": "running"}]
        assert details.active_jobs == {
            "late-job": {"id": "late-job", "progress": 99}
        }
        assert details.counts == snapshot.stats
    finally:
        runner.release.set()
        assert manager.wait(task.id, timeout=1)


def test_stopping_task_rejects_late_reporter_mutations_and_keeps_cancel_state_stable(
    tmp_path,
):
    store, account_id = _new_store(tmp_path)
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, persistence=store)

    task = manager.start(**_task_inputs(account_id))
    try:
        assert runner.entered.wait(timeout=1)
        assert manager.cancel(task.id).state == "stopping"
        before_snapshot = manager.get_snapshot(task.id)
        before_details = manager.get_details(task.id)
        before_db = store.load_web_tasks(limit=10)

        _publish_all_reporter_mutations(runner.reporter)

        assert manager.get_snapshot(task.id) == before_snapshot
        assert manager.get_snapshot(task.id).state == "stopping"
        assert manager.get_details(task.id) == before_details
        assert store.load_web_tasks(limit=10) == before_db
    finally:
        runner.release.set()
        assert manager.wait(task.id, timeout=1)
    assert manager.get_snapshot(task.id).state == "stopped"
