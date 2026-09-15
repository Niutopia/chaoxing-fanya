"""Deterministic tests for concurrent account task state and task logs."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

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


class ReturnBoundaryRunner:
    """Expose the tiny return/finalization window without sleeping."""

    def __init__(self):
        self.manager = None
        self.context = None
        self.cancel_check_reached = threading.Event()
        self.release_cancel_check = threading.Event()

    def run(self, context):
        self.context = context
        original_is_set = context.cancel_event.is_set

        def gated_is_set():
            # The pre-fix worker checks outside the manager lock.  The fixed
            # worker checks while holding it; return the value observed before
            # cancellation only for the former path, and re-read for the
            # latter path after the barrier releases.
            under_manager_lock = self.manager.lock._is_owned()
            observed = original_is_set()
            self.cancel_check_reached.set()
            assert self.release_cancel_check.wait(timeout=2)
            return original_is_set() if under_manager_lock else observed

        context.cancel_event.is_set = gated_is_set


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


def test_cancel_between_runner_return_and_finalization_reaches_stopped(task_inputs):
    runner = ReturnBoundaryRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    runner.manager = manager
    task = manager.start(**task_inputs("account-a"))

    assert runner.cancel_check_reached.wait(timeout=1)
    runner.context.cancel_event.set()
    runner.release_cancel_check.set()
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


def test_worker_exception_propagates_after_job_processor_shutdown(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "process_chapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("worker-boom")
        ),
    )
    config = {
        "speed": 1.0,
        "jobs": 1,
        "notopen_action": "continue",
        "task_id": "worker-error-task",
        "cancel_event": threading.Event(),
    }
    processor = main.JobProcessor(
        SimpleNamespace(),
        {"title": "course"},
        [
            main.ChapterTask(
                index=0,
                point={"title": "chapter", "has_finished": False},
            )
        ],
        config,
    )
    with pytest.raises(RuntimeError, match="worker-boom"):
        processor.run()


def test_ocr_secret_is_redacted_from_worker_failure_and_logs(
    monkeypatch, task_inputs, capsys
):
    import main
    from api.logger import logger
    from webapp.task_logging import install_task_log_sink, remove_task_log_sink

    secret = "ocr-secret"
    entered = threading.Event()
    release = threading.Event()

    def run(context):
        entered.set()
        assert release.wait(timeout=2)
        config = {
            "speed": 1.0,
            "jobs": 1,
            "notopen_action": "continue",
            "task_id": context.task_id,
            "cancel_event": context.cancel_event,
            "ocr_config": context.preferences.ocr_config,
        }
        processor = main.JobProcessor(
            SimpleNamespace(),
            {"title": "course"},
            [
                main.ChapterTask(
                    index=0,
                    point={"title": "chapter", "has_finished": False},
                )
            ],
            config,
        )
        processor.run()

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"worker failed with {secret}")

    monkeypatch.setattr(main, "process_chapter", fail)
    values = task_inputs("ocr-account")
    values["preferences"] = values["preferences"].__class__(
        **{
            **values["preferences"].__dict__,
            "ocr_config": {"provider": "openai", "api_key": secret},
        }
    )
    manager = TaskManager(runner=run, max_active_accounts=1)
    task = manager.start(**values)
    assert entered.wait(timeout=1)
    sink_id = install_task_log_sink(manager)
    seen = []
    capture_id = logger.add(
        lambda message: seen.append(message.record["message"]), enqueue=False
    )
    try:
        release.set()
        manager.wait(task.id, timeout=2)
        snapshot = manager.get_snapshot(task.id)
        logs = manager.get_logs(task.id).items
    finally:
        logger.remove(capture_id)
        remove_task_log_sink(sink_id)
    captured = capsys.readouterr()
    assert snapshot.state == "failed"
    assert secret not in (snapshot.error or "")
    assert all(secret not in item.message for item in logs)
    assert all(secret not in item for item in seen)
    assert secret not in captured.out
    assert secret not in captured.err


def test_notification_secrets_are_redacted_from_snapshot_details_logs_and_error(
    task_inputs,
):
    notification_url = "https://notify.example.invalid/bot/notification-url-secret"
    chat_id = "telegram-chat-id-secret"
    token = "provider-token-secret"
    nested_alias = "nested-secret-alias"

    class FailureRunner:
        def run(self, context):
            context.reporter.set_current(course=notification_url)
            context.reporter.set_counts(notification_url=notification_url)
            context.reporter.set_courses(
                [{"id": "course", "notification": {"url": notification_url}}]
            )
            context.reporter.set_active_jobs(
                {"job": {"chat_id": chat_id, "nested": {"token": token}}}
            )
            context.reporter.append_log(
                f"notify {notification_url} {chat_id} {token} {nested_alias}"
            )
            raise RuntimeError(
                f"notification failed: {notification_url} {chat_id} {token} {nested_alias}"
            )

    values = task_inputs("notification-account")
    values["preferences"] = replace(
        values["preferences"],
        notification_config={
            "provider": "Telegram",
            "url": notification_url,
            "tg_chat_id": chat_id,
            "nested": {"token": token, "secret_alias": nested_alias},
        },
    )
    manager = TaskManager(runner=FailureRunner(), max_active_accounts=1)
    task = manager.start(**values)
    assert manager.wait(task.id, timeout=1)

    visible = " ".join(
        [
            repr(manager.get_snapshot(task.id)),
            repr(manager.get_details(task.id)),
            repr(manager.get_logs(task.id).items),
        ]
    )
    for secret in (notification_url, chat_id, token, nested_alias):
        assert secret not in visible


def test_new_task_keeps_prior_terminal_record_and_owned_state(task_inputs):
    runner = OutcomeRunner("completed")
    manager = TaskManager(runner=runner, max_active_accounts=1)
    first = manager.start(**task_inputs("evict-account"))
    assert manager.wait(first.id, timeout=1)
    assert manager.get_snapshot(first.id).state == "completed"

    second = manager.start(**task_inputs("evict-account"))
    assert manager.wait(second.id, timeout=1)
    assert [snapshot.id for snapshot in manager.list_tasks()] == [second.id, first.id]
    assert manager.get_snapshot(first.id).state == "completed"
    assert manager.get_details(first.id).courses == []
    assert manager.get_logs(first.id).items == []


def test_terminal_task_releases_decrypted_credentials(task_inputs):
    runner = OutcomeRunner("completed")
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("scrub-account"))
    assert manager.wait(task.id, timeout=1)
    context = manager.get_context(task.id)
    assert context.auth.username == ""
    assert context.auth.password == ""
    assert context.auth.cookies == {}
    assert context.answer.api_key is None
    assert context.preferences.notification_config == {}
    assert context.preferences.ocr_config == {}


def test_late_terminal_log_is_dropped_after_secret_scrub(task_inputs):
    runner = OutcomeRunner("completed")
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("late-log-account"))
    assert manager.wait(task.id, timeout=1)

    late_secret = "late-log-secret-that-must-not-be-retained"
    assert manager.append_log(task.id, late_secret) is None
    assert manager.get_logs(task.id).items == []


def test_terminal_task_history_is_globally_bounded(task_inputs):
    runner = OutcomeRunner("completed")
    manager = TaskManager(
        runner=runner,
        max_active_accounts=1,
        terminal_task_capacity=2,
    )
    ids = []
    for account in ("a", "b", "c"):
        task = manager.start(**task_inputs(account))
        assert manager.wait(task.id, timeout=1)
        ids.append(task.id)
    assert [item.id for item in manager.list_tasks()] == list(reversed(ids[-2:]))
