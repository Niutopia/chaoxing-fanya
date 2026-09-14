"""Process-local task admission, state, progress, and log storage.

The Web application deliberately keeps task state in memory.  A task owns its
own cancellation event, reporter, details, and bounded log buffer, while a
manager-wide semaphore limits the number of account tasks that may run at one
time.  No credential-bearing value is ever included in a public snapshot,
details object, or log entry.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from api.live_process import StudyCancelled

from .models import (
    AccountAuth,
    AccountPreferences,
    ResolvedAnswerConnection,
    TaskDetails,
    TaskLogEntry,
    TaskLogPage,
    TaskSnapshot,
    TaskState,
)


DEFAULT_LOG_CAPACITY = 5_000
_UNSET = object()


class TaskManagerError(RuntimeError):
    """Base class for task admission and lookup failures."""


class AccountTaskConflict(TaskManagerError):
    """Raised when an account already owns a running or stopping task."""


class TaskCapacityReached(TaskManagerError):
    """Raised when the global active-account limit has no free slot."""


class TaskNotFound(KeyError, TaskManagerError):
    """Raised when a task ID is not known in this process."""


class TaskNotRunning(TaskManagerError):
    """Raised when an operation requires a task that is still running."""


def _copy(value: Any) -> Any:
    """Copy caller-owned values without allowing mutable aliases to escape."""

    try:
        return copy.deepcopy(value)
    except Exception:
        # A reporter should never be able to break task bookkeeping because a
        # third-party mapping has an unusual ``__deepcopy__`` implementation.
        if isinstance(value, Mapping):
            return {key: _copy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_copy(item) for item in value)
        return value


def _account_preferences(value: AccountPreferences | Mapping[str, Any]) -> AccountPreferences:
    """Return an account-preference copy suitable for a task context."""

    if isinstance(value, AccountPreferences):
        return AccountPreferences(
            selected_course_ids=list(value.selected_course_ids),
            speed=value.speed,
            jobs=value.jobs,
            notopen_action=value.notopen_action,
            answer_enabled=value.answer_enabled,
            answer_cover_rate=value.answer_cover_rate,
            answer_auto_submit=value.answer_auto_submit,
            notification_config=_copy(value.notification_config),
            ocr_config=_copy(value.ocr_config),
        )
    if isinstance(value, Mapping):
        return AccountPreferences(**_copy(dict(value)))
    raise TypeError("preferences must be AccountPreferences or a mapping")


def _account_auth(value: AccountAuth | Mapping[str, Any]) -> AccountAuth:
    """Return a task-owned copy of resolved credentials."""

    if isinstance(value, AccountAuth):
        return AccountAuth(
            username=value.username,
            password=value.password,
            cookies=_copy(value.cookies),
        )
    if isinstance(value, Mapping):
        return AccountAuth(**_copy(dict(value)))
    raise TypeError("auth must be AccountAuth or a mapping")


def _answer_connection(value: ResolvedAnswerConnection | Mapping[str, Any] | None):
    """Copy an optional resolved answer connection, including its key."""

    if value is None:
        return None
    if isinstance(value, ResolvedAnswerConnection):
        return ResolvedAnswerConnection(
            enabled=value.enabled,
            base_url=value.base_url,
            model=value.model,
            has_api_key=value.has_api_key,
            timeout_seconds=value.timeout_seconds,
            max_retries=value.max_retries,
            max_concurrency=value.max_concurrency,
            api_key=value.api_key,
        )
    if isinstance(value, Mapping):
        return ResolvedAnswerConnection(**_copy(dict(value)))
    # Keep the public type contract strict.  In particular, do not silently
    # retain an arbitrary object whose repr could expose a credential.
    raise TypeError("answer must be ResolvedAnswerConnection, mapping, or None")


def _secret_values(auth: AccountAuth, answer: Any) -> tuple[str, ...]:
    values: list[str] = []
    password = getattr(auth, "password", None)
    if password:
        values.append(str(password))
    cookies = getattr(auth, "cookies", {})
    if isinstance(cookies, Mapping):
        values.extend(str(item) for item in cookies.values() if item)
    api_key = getattr(answer, "api_key", None)
    if api_key:
        values.append(str(api_key))
    # Remove duplicates while retaining deterministic replacement order.  Do
    # not discard short values: a cookie/token can technically be one byte,
    # and keeping it out of task logs is more important than preserving prose.
    return tuple(dict.fromkeys(values))


_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "cookie",
        "cookies",
        "api_key",
        "apikey",
        "authorization",
        "token",
        "secret",
    }
)


def _redact_text(value: Any, secrets: tuple[str, ...]) -> str:
    try:
        text = str(value)
    except Exception:
        text = "[unavailable]"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _redact(value: Any, secrets: tuple[str, ...], *, key: Any = None) -> Any:
    """Copy and redact known secrets from reporter-owned nested values."""

    key_name = str(key).strip().lower() if key is not None else ""
    if key_name in _SENSITIVE_KEYS or any(
        marker in key_name for marker in ("password", "cookie", "api_key", "token", "secret")
    ):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            _copy(item_key): _redact(item_value, secrets, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, secrets) for item in value)
    if isinstance(value, set):
        return {_redact(item, secrets) for item in value}
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return _copy(value)


def _timestamp(value: Any = None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return time.time()


@dataclass(frozen=True, repr=False)
class StudyRunContext:
    """Immutable task inputs handed to the study runner.

    ``auth`` and a resolved answer connection are intentionally present here:
    they are needed by the runner, but are never copied into public task
    snapshots or log records.  The custom representation avoids accidentally
    printing those secrets while debugging a worker.
    """

    task_id: str
    account_id: str
    course_ids: list[str]
    preferences: AccountPreferences
    auth: AccountAuth
    answer: ResolvedAnswerConnection | None
    answer_semaphore: threading.Semaphore | None
    cancel_event: threading.Event
    reporter: "TaskReporter"

    def __repr__(self) -> str:
        return (
            "StudyRunContext("
            f"task_id={self.task_id!r}, account_id={self.account_id!r}, "
            f"course_ids={self.course_ids!r}, preferences=<redacted>, "
            "auth=<redacted>, answer=<redacted>, "
            f"answer_semaphore={self.answer_semaphore!r}, "
            f"cancel_event={self.cancel_event!r}, reporter=<TaskReporter>)"
        )


@dataclass
class _TaskRuntime:
    snapshot: TaskSnapshot
    details: TaskDetails
    context: StudyRunContext
    done: threading.Event
    started: threading.Event
    logs: deque[TaskLogEntry]
    next_sequence: int = 0
    slot_released: bool = False
    thread: threading.Thread | None = None


class TaskReporter:
    """Thread-safe progress/detail/log update facade for one task."""

    def __init__(self, manager: "TaskManager", task_id: str):
        self._manager = manager
        self.task_id = str(task_id)

    def set_current(
        self,
        course: Any = _UNSET,
        chapter: Any = _UNSET,
        task: Any = _UNSET,
        **values: Any,
    ) -> None:
        """Set current course/chapter/task labels.

        The keyword aliases are intentionally accepted because legacy engine
        callbacks use both ``task`` and ``job`` terminology.
        """

        supplied: dict[str, Any] = {}
        if course is not _UNSET:
            supplied["current_course"] = course
        elif "current_course" in values:
            supplied["current_course"] = values["current_course"]
        if chapter is not _UNSET:
            supplied["current_chapter"] = chapter
        elif "current_chapter" in values:
            supplied["current_chapter"] = values["current_chapter"]
        if task is not _UNSET:
            supplied["current_task"] = task
        elif "current_task" in values:
            supplied["current_task"] = values["current_task"]
        elif "job" in values:
            supplied["current_task"] = values["job"]
        self._manager._set_current(self.task_id, supplied)

    def set_counts(self, *args: Any, **counts: Any) -> None:
        """Update aggregate progress counts.

        ``set_counts(progress=..., total=...)`` is the compact form used by
        simple runners.  Named course/chapter/task counts are retained in the
        public ``stats``/``counts`` mappings and infer progress/total when the
        compact fields are omitted.
        """

        if len(args) > 2:
            raise TypeError("set_counts accepts a mapping or progress and total")
        if len(args) == 2:
            if "progress" in counts or "total" in counts:
                raise TypeError("progress/total supplied twice")
            counts = {"progress": args[0], "total": args[1], **counts}
        elif args:
            if not isinstance(args[0], Mapping):
                raise TypeError("set_counts positional value must be a mapping")
            merged = dict(args[0])
            merged.update(counts)
            counts = merged
        self._manager._set_counts(self.task_id, counts)

    def set_courses(self, courses: Any) -> None:
        self._manager._set_courses(self.task_id, courses)

    def set_active_jobs(self, active_jobs: Any) -> None:
        self._manager._set_active_jobs(self.task_id, active_jobs)

    def append_log(
        self,
        message: Any,
        level: str = "info",
        timestamp: Any = None,
    ) -> TaskLogEntry | None:
        return self._manager.append_log(
            self.task_id,
            message,
            level,
            timestamp=timestamp,
        )


class TaskManager:
    """Own all process-local task state and coordinate account admission."""

    def __init__(
        self,
        runner: Callable[[StudyRunContext], Any] | Any | None = None,
        max_active_accounts: int = 3,
        *,
        answer_semaphore: threading.Semaphore | None = None,
        log_capacity: int = DEFAULT_LOG_CAPACITY,
    ) -> None:
        if (
            isinstance(max_active_accounts, bool)
            or not isinstance(max_active_accounts, int)
            or not 1 <= max_active_accounts <= 10
        ):
            raise ValueError("max_active_accounts must be between 1 and 10")
        if (
            isinstance(log_capacity, bool)
            or not isinstance(log_capacity, int)
            or log_capacity < 1
        ):
            raise ValueError("log_capacity must be a positive integer")
        self.runner = runner
        self.max_active_accounts = max_active_accounts
        self.answer_semaphore = answer_semaphore
        self.log_capacity = log_capacity
        self._lock = threading.RLock()
        # Keep this name public-ish: account routes use the manager as their
        # active-task guard, and diagnostics benefit from a simple mapping.
        self.active_by_account: dict[str, str] = {}
        self._tasks: dict[str, _TaskRuntime] = {}
        self._active_slots = threading.BoundedSemaphore(max_active_accounts)

    @property
    def lock(self) -> threading.RLock:
        """Expose the manager lock for carefully scoped integrations."""

        return self._lock

    @property
    def semaphore(self) -> threading.BoundedSemaphore:
        return self._active_slots

    def _lookup(self, task_id: str) -> _TaskRuntime:
        record = self._tasks.get(str(task_id))
        if record is None:
            raise TaskNotFound(str(task_id))
        return record

    def _secrets_for(self, record: _TaskRuntime) -> tuple[str, ...]:
        return _secret_values(record.context.auth, record.context.answer)

    def start(
        self,
        account_id: str,
        course_ids: list[str],
        preferences: AccountPreferences,
        auth: AccountAuth,
        answer: ResolvedAnswerConnection | None = None,
    ) -> TaskSnapshot:
        """Admit and asynchronously start one account-owned study task."""

        account = str(account_id)
        if not account.strip():
            raise ValueError("account_id must not be blank")
        if course_ids is None:
            raise TypeError("course_ids must be a list")
        # Resolve and copy all caller-owned values before acquiring a global
        # slot.  Invalid input must not strand a semaphore permit.
        copied_course_ids = [str(item) for item in list(course_ids)]
        copied_preferences = _account_preferences(preferences)
        copied_auth = _account_auth(auth)
        copied_answer = _answer_connection(answer)

        with self._lock:
            if account in self.active_by_account:
                raise AccountTaskConflict(account)
            if not self._active_slots.acquire(blocking=False):
                raise TaskCapacityReached("maximum active account tasks reached")

            task_id = str(uuid.uuid4())
            now = time.time()
            reporter = TaskReporter(self, task_id)
            context = StudyRunContext(
                task_id=task_id,
                account_id=account,
                course_ids=copied_course_ids,
                preferences=copied_preferences,
                auth=copied_auth,
                answer=copied_answer,
                answer_semaphore=self.answer_semaphore,
                cancel_event=threading.Event(),
                reporter=reporter,
            )
            snapshot = TaskSnapshot(
                id=task_id,
                account_id=account,
                state="running",
                progress=0,
                total=len(context.course_ids),
                started_at=now,
            )
            record = _TaskRuntime(
                snapshot=snapshot,
                details=TaskDetails(),
                context=context,
                done=threading.Event(),
                started=threading.Event(),
                logs=deque(maxlen=self.log_capacity),
            )
            self._tasks[task_id] = record
            self.active_by_account[account] = task_id

            thread = threading.Thread(
                target=self._run_task,
                args=(task_id,),
                name=f"chaoxing-task-{task_id[:8]}",
                daemon=True,
            )
            record.thread = thread
            try:
                thread.start()
            except BaseException as exc:
                # Thread creation failure is unusual, but admission must not
                # permanently consume a global slot or account mapping.
                self._finish_locked(record, "failed", self._sanitize(record, exc))
                raise

        # Waiting for the wrapper's first instruction makes start deterministic
        # for callers that immediately inspect fake-runner state, without
        # waiting for the runner itself (which may intentionally block).
        record.started.wait()
        return self._snapshot_copy(record.snapshot)

    def _invoke_runner(self, context: StudyRunContext) -> Any:
        runner = self.runner
        if runner is None:
            return None
        target = getattr(runner, "run", None)
        if callable(target):
            return target(context)
        if callable(runner):
            return runner(context)
        raise TypeError("runner must be callable or expose run(context)")

    def _run_task(self, task_id: str) -> None:
        # Import lazily so importing TaskManager does not force Loguru setup in
        # command-line code that only wants the value objects.
        from .task_logging import run_with_task_context

        with self._lock:
            record = self._lookup(task_id)
            record.started.set()
            context = record.context
        state: TaskState = "completed"
        error: str | None = None
        normal_return = False
        try:
            run_with_task_context(task_id, self._invoke_runner, context)
            normal_return = True
        except StudyCancelled:
            # The study engine raises this only at a cooperative safe
            # boundary.  Treat it as the requested terminal state rather than
            # exposing cancellation as a failed task.
            state = "stopped"
            error = None
        except BaseException as exc:
            state = "failed"
            with self._lock:
                # The task record remains available for terminal inspection;
                # only the public error text is retained after redaction.
                error = self._sanitize(record, exc)
        finally:
            with self._lock:
                # Select the terminal state while holding the same lock used
                # by ``cancel``.  This closes the return/finalization race:
                # cancellation that wins before finalization is observed and
                # produces stopping -> stopped, while a cancel arriving after
                # a completed transition simply receives that terminal state.
                if normal_return:
                    state = "stopped" if context.cancel_event.is_set() else "completed"
                self._finish_locked(record, state, error)

    def _sanitize(self, record: _TaskRuntime, value: Any) -> str:
        text = _redact_text(value, self._secrets_for(record))
        return text or "Task failed"

    @staticmethod
    def _snapshot_copy(snapshot: TaskSnapshot) -> TaskSnapshot:
        return replace(snapshot, stats=_copy(snapshot.stats))

    @staticmethod
    def _details_copy(details: TaskDetails) -> TaskDetails:
        return TaskDetails(
            courses=_copy(details.courses),
            active_jobs=_copy(details.active_jobs),
            counts=_copy(details.counts),
        )

    def _finish_locked(
        self,
        record: _TaskRuntime,
        state: TaskState,
        error: str | None,
    ) -> None:
        if record.done.is_set():
            return
        record.snapshot = replace(
            record.snapshot,
            state=state,
            error=error,
            finished_at=time.time(),
            stats=_copy(record.snapshot.stats),
        )
        if self.active_by_account.get(record.snapshot.account_id) == record.snapshot.id:
            self.active_by_account.pop(record.snapshot.account_id, None)
        if not record.slot_released:
            record.slot_released = True
            self._active_slots.release()
        record.done.set()

    def cancel(self, task_id: str) -> TaskSnapshot:
        """Request cooperative cancellation and return the new snapshot."""

        with self._lock:
            record = self._lookup(task_id)
            if record.snapshot.state in {"running", "stopping"}:
                if record.snapshot.state == "running":
                    record.snapshot = replace(record.snapshot, state="stopping")
                record.context.cancel_event.set()
            return self._snapshot_copy(record.snapshot)

    def wait(self, task_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            record = self._lookup(task_id)
        return record.done.wait(timeout)

    def wait_until_started(self, task_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            record = self._lookup(task_id)
        return record.started.wait(timeout)

    def has_active_task(self, account_id: str) -> bool:
        with self._lock:
            task_id = self.active_by_account.get(str(account_id))
            if task_id is None:
                return False
            record = self._tasks.get(task_id)
            return bool(
                record is not None
                and record.snapshot.state in {"running", "stopping"}
            )

    def list_tasks(self) -> list[TaskSnapshot]:
        with self._lock:
            return [self._snapshot_copy(item.snapshot) for item in self._tasks.values()]

    def get_snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            return self._snapshot_copy(self._lookup(task_id).snapshot)

    def get_details(self, task_id: str) -> TaskDetails:
        with self._lock:
            return self._details_copy(self._lookup(task_id).details)

    def get_context(self, task_id: str) -> StudyRunContext:
        """Return the internal context for deterministic runner integrations."""

        with self._lock:
            return self._lookup(task_id).context

    def get_reporter(self, task_id: str) -> TaskReporter:
        with self._lock:
            return self._lookup(task_id).context.reporter

    def _set_current(self, task_id: str, values: Mapping[str, Any]) -> None:
        with self._lock:
            record = self._lookup(task_id)
            if not values:
                return
            secrets = self._secrets_for(record)
            updates = {
                key: _redact(value, secrets)
                for key, value in values.items()
                if key in {"current_course", "current_chapter", "current_task"}
            }
            record.snapshot = replace(record.snapshot, **updates)

    @staticmethod
    def _infer_progress(counts: Mapping[str, Any]) -> tuple[Any, Any]:
        progress = counts.get("progress")
        total = counts.get("total")
        if progress is None:
            for name in (
                "completed_courses",
                "completed_chapters",
                "completed_tasks",
                "completed",
            ):
                if name in counts:
                    progress = counts[name]
                    break
        if total is None:
            for name in (
                "total_courses",
                "total_chapters",
                "total_tasks",
            ):
                if name in counts:
                    total = counts[name]
                    break
        return progress, total

    def _set_counts(self, task_id: str, values: Mapping[str, Any]) -> None:
        with self._lock:
            record = self._lookup(task_id)
            secrets = self._secrets_for(record)
            copied = _redact(dict(values), secrets)
            if not isinstance(copied, dict):
                copied = {}
            merged_counts = dict(record.details.counts)
            merged_counts.update(copied)
            progress, total = self._infer_progress(merged_counts)
            record.snapshot = replace(
                record.snapshot,
                stats=_copy(merged_counts),
                **({"progress": progress} if progress is not None else {}),
                **({"total": total} if total is not None else {}),
            )
            record.details = replace(record.details, counts=_copy(merged_counts))

    def _set_courses(self, task_id: str, courses: Any) -> None:
        with self._lock:
            record = self._lookup(task_id)
            value = [] if courses is None else courses
            copied = _redact(value, self._secrets_for(record))
            if not isinstance(copied, list):
                copied = list(copied) if isinstance(copied, (tuple, set)) else [copied]
            record.details = replace(record.details, courses=copied)

    def _set_active_jobs(self, task_id: str, active_jobs: Any) -> None:
        with self._lock:
            record = self._lookup(task_id)
            value = {} if active_jobs is None else active_jobs
            copied = _redact(value, self._secrets_for(record))
            if not isinstance(copied, dict):
                copied = {}
            record.details = replace(record.details, active_jobs=copied)

    def append_log(
        self,
        task_id: str,
        message: Any,
        level: str = "info",
        timestamp: Any = None,
    ) -> TaskLogEntry | None:
        """Append one redacted entry, preserving per-task order.

        Unknown task IDs are ignored.  This is important for the global
        Loguru sink: a late queued record from a task that has already been
        torn down must never turn logging itself into an application error.
        """

        with self._lock:
            record = self._tasks.get(str(task_id))
            if record is None:
                return None
            record.next_sequence += 1
            entry = TaskLogEntry(
                sequence=record.next_sequence,
                level=_redact_text(level, self._secrets_for(record)).lower(),
                message=self._sanitize(record, message),
                timestamp=_timestamp(timestamp),
            )
            record.logs.append(entry)
            return entry

    def get_logs(self, task_id: str, after: int = 0) -> TaskLogPage:
        """Read task logs without consuming or mutating the buffer."""

        if after is None:
            after = 0
        if isinstance(after, bool) or not isinstance(after, int):
            raise ValueError("after cursor must be a non-negative integer")
        if after < 0:
            raise ValueError("after cursor must be a non-negative integer")
        with self._lock:
            record = self._lookup(task_id)
            items = [
                item
                for item in record.logs
                if item.sequence > after
            ]
            next_cursor = max(after, record.next_sequence)
            return TaskLogPage(items=_copy(items), next_cursor=next_cursor)


# Re-exporting the helper keeps the task-facing API convenient while the
# implementation remains in the dedicated logging module.
from .task_logging import run_with_task_context


__all__ = [
    "AccountTaskConflict",
    "DEFAULT_LOG_CAPACITY",
    "StudyRunContext",
    "TaskCapacityReached",
    "TaskDetails",
    "TaskLogEntry",
    "TaskLogPage",
    "TaskManager",
    "TaskManagerError",
    "TaskNotFound",
    "TaskNotRunning",
    "TaskReporter",
    "TaskSnapshot",
    "TaskState",
    "run_with_task_context",
]
