"""Deterministic tests for concurrent account task state and task logs."""

from __future__ import annotations

import threading

import pytest

from webapp.models import AccountAuth, AccountPreferences, ResolvedAnswerConnection
from webapp.task_manager import (
    AccountTaskConflict,
    TaskCapacityReached,
    TaskManager,
)


class BlockingRunner:
    def __init__(self):
        self.started = {}
        self.release = threading.Event()

    def run(self, context):
        self.started[context.account_id] = threading.current_thread().name
        context.reporter.set_current(course="Course", chapter="Chapter")
        self.release.wait(timeout=2)


class ControllableRunner:
    def __init__(self):
        self.contexts = {}
        self._release = {}
        self._lock = threading.Lock()

    def run(self, context):
        with self._lock:
            self.contexts[context.task_id] = context
            release = self._release.setdefault(context.task_id, threading.Event())
        release.wait(timeout=2)
        if context.cancel_event.is_set():
            return

    def complete(self, task_id):
        with self._lock:
            self._release.setdefault(task_id, threading.Event()).set()

    def acknowledge_cancel(self, task_id):
        self.complete(task_id)


class OutcomeRunner:
    def __init__(self, outcome):
        self.outcome = outcome

    def run(self, context):
        if self.outcome == "failed":
            raise RuntimeError("safe runner failure")
        if self.outcome == "stopped":
            context.cancel_event.set()


@pytest.fixture
def task_inputs():
    def make(account_id):
        return {
            "account_id": account_id,
            "course_ids": ["course-1"],
            "preferences": AccountPreferences(selected_course_ids=["course-1"]),
            "auth": AccountAuth(
                username=f"user-{account_id}",
                password="password-never-in-task-output",
                cookies={"sid": "cookie-never-in-task-output"},
            ),
            "answer": ResolvedAnswerConnection(
                enabled=True,
                has_api_key=True,
                api_key="answer-key-never-in-task-output",
            ),
        }

    return make


@pytest.fixture
def manager_with_running_tasks(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=2)
    first = manager.start(**task_inputs("account-a"))
    second = manager.start(**task_inputs("account-b"))
    return manager, first, second, runner


def test_different_accounts_run_concurrently(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=3)
    first = manager.start(**task_inputs("account-a"))
    second = manager.start(**task_inputs("account-b"))
    assert first.account_id == "account-a"
    assert second.account_id == "account-b"
    assert set(runner.started) == {"account-a", "account-b"}
    assert all(name.startswith("chaoxing-task-") for name in runner.started.values())
    runner.release.set()
    manager.wait(first.id, timeout=1)
    manager.wait(second.id, timeout=1)


def test_same_account_cannot_start_twice(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=3)
    manager.start(**task_inputs("account-a"))
    with pytest.raises(AccountTaskConflict):
        manager.start(**task_inputs("account-a"))
    runner.release.set()


def test_global_account_limit_is_released_after_terminal_task(task_inputs):
    runner = ControllableRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    first = manager.start(**task_inputs("account-a"))
    with pytest.raises(TaskCapacityReached):
        manager.start(**task_inputs("account-b"))
    runner.complete(first.id)
    manager.wait(first.id, timeout=1)
    second = manager.start(**task_inputs("account-b"))
    assert second.state == "running"
    runner.complete(second.id)
    manager.wait(second.id, timeout=1)


def test_cancel_delivers_event_and_reaches_stopped(task_inputs):
    runner = ControllableRunner()
    manager = TaskManager(runner=runner, max_active_accounts=2)
    task = manager.start(**task_inputs("account-a"))
    manager.wait_until_started(task.id, timeout=1)
    assert manager.cancel(task.id).state == "stopping"
    assert runner.contexts[task.id].cancel_event.is_set()
    runner.acknowledge_cancel(task.id)
    manager.wait(task.id, timeout=1)
    assert manager.get_snapshot(task.id).state == "stopped"


@pytest.mark.parametrize("outcome", ["completed", "failed", "stopped"])
def test_terminal_snapshot_replaces_previous_account_snapshot(task_inputs, outcome):
    runner = OutcomeRunner(outcome)
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("account-a"))
    manager.wait(task.id, timeout=1)
    assert manager.list_tasks()[0].state == outcome
    assert manager.has_active_task("account-a") is False


def test_logs_are_partitioned_and_cursor_based(manager_with_running_tasks):
    manager, first, second, runner = manager_with_running_tasks
    manager.append_log(first.id, "first-only", "info")
    manager.append_log(second.id, "second-only", "warning")
    first_page = manager.get_logs(first.id, after=0)
    second_page = manager.get_logs(second.id, after=0)
    assert [item.message for item in first_page.items] == ["first-only"]
    assert [item.message for item in second_page.items] == ["second-only"]
    assert manager.get_logs(first.id, after=0).items == first_page.items
    assert manager.get_logs(first.id, after=first_page.next_cursor).items == []
    runner.release.set()


def test_logs_keep_newest_bounded_entries_with_monotonic_sequences(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("account-a"))
    for index in range(5_010):
        manager.append_log(task.id, f"entry-{index}", "info")
    page = manager.get_logs(task.id, after=0)
    assert len(page.items) == 5_000
    assert page.items[0].message == "entry-10"
    assert page.items[-1].message == "entry-5009"
    assert [entry.sequence for entry in page.items] == list(range(11, 5_011))
    runner.release.set()


def test_reporter_copies_caller_owned_details(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("account-a"))
    courses = [{"id": "course-a", "chapters": [{"id": "chapter-a"}]}]
    active_jobs = {"job-a": {"progress": 10}}
    task_details = {"custom": ["value"]}
    runner.release.set()
    manager.wait_until_started(task.id, timeout=1)
    context = manager.get_context(task.id)
    context.reporter.set_courses(courses)
    context.reporter.set_active_jobs(active_jobs)
    courses[0]["chapters"].append({"id": "mutated"})
    active_jobs["job-a"]["progress"] = 99
    assert manager.get_details(task.id).courses == [
        {"id": "course-a", "chapters": [{"id": "chapter-a"}]}
    ]
    assert manager.get_details(task.id).active_jobs == {"job-a": {"progress": 10}}
    context.reporter.set_counts(custom=task_details["custom"])
    task_details["custom"].append("mutated")
    assert manager.get_details(task.id).counts["custom"] == ["value"]


def test_run_with_task_context_adds_task_id_to_log_context(task_inputs):
    from api.logger import logger
    from webapp.task_logging import run_with_task_context

    seen = {}

    def target():
        logger.info("context-only")

    sink_id = logger.add(
        lambda message: seen.setdefault("task_id", message.record["extra"].get("task_id")),
        enqueue=False,
    )
    try:
        run_with_task_context("task-id", target)
    finally:
        logger.remove(sink_id)
    assert seen == {"task_id": "task-id"}
