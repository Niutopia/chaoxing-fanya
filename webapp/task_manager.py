"""Process-local task execution with optional monitor persistence.

A task owns its own cancellation event, reporter, details, and bounded log
buffer, while a manager-wide semaphore limits the number of account tasks that
may run at one time.  A persistence adapter can mirror the public monitor
values to durable storage; only credential-free snapshots, details, and logs
cross that boundary.  No credential-bearing value is ever included in a
public snapshot, details object, or log entry.
"""

from __future__ import annotations

import copy
import heapq
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from api.live_process import StudyCancelled
from api.logger import (
    sanitize_log_extra,
    sanitize_log_level,
    sanitize_log_message,
    validate_task_id,
)

from .limits import (
    MAX_CONFIG_DEPTH,
    MAX_CONFIG_LIST_LENGTH,
    MAX_CONFIG_NODES,
    MAX_COURSE_ID_LENGTH,
    MAX_SELECTED_COURSE_IDS,
)
from .config_security import is_sensitive_config_key
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
DEFAULT_TERMINAL_TASK_CAPACITY = 100
# A custom persistence adapter can provide an iterable with no reliable end.
# Keep its restore traversal explicitly finite.  SQLiteStore supplies an
# ordered, already-bounded hint and uses the fast path below; untrusted custom
# values are scanned by index/iterator only within this budget.
_MAX_CUSTOM_LOG_SCAN = 10_000
_RESTART_INTERRUPTION_ERROR = "服务重启导致任务中断，超星进度保留，可重新开始继续"
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


def _json_compatible(value: Any) -> Any:
    """Detach public monitor values into the store's JSON-compatible shape."""

    return sanitize_log_extra(value)


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
            auth_mode=value.auth_mode,
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
            outbound_base_url=value.outbound_base_url,
        )
    if isinstance(value, Mapping):
        return ResolvedAnswerConnection(**_copy(dict(value)))
    # Keep the public type contract strict.  In particular, do not silently
    # retain an arbitrary object whose repr could expose a credential.
    raise TypeError("answer must be ResolvedAnswerConnection, mapping, or None")


def _config_secret_values(config: Any) -> list[str]:
    """Collect secret-bearing values from an account-owned config mapping."""

    values: list[str] = []
    # ``validate_config_shape`` normally rejects cycles and oversized input at
    # the account boundary, but task contexts can also be built directly by
    # integrations/tests.  Keep this collector independently bounded so a
    # hostile provider mapping cannot recurse forever or consume unbounded
    # memory while a worker is starting.
    visited: set[int] = set()
    nodes = 0

    def walk(value: Any, secret_scope: bool = False, depth: int = 0) -> None:
        nonlocal nodes
        if depth > MAX_CONFIG_DEPTH or nodes >= MAX_CONFIG_NODES:
            return
        container = isinstance(value, Mapping) or isinstance(
            value, (list, tuple, set, frozenset)
        )
        if not container:
            nodes += 1
            if not secret_scope or not isinstance(value, (str, int, float, bool)):
                return
            if value == "":
                return
            try:
                values.append(str(value))
            except BaseException:
                return
            return
        if container:
            marker = id(value)
            # Track only the active path, not every object ever seen.  A
            # shared alias can occur once below a sensitive key and once below
            # a normal key; rescanning that alias is necessary to collect the
            # sensitive path without sacrificing cycle protection.
            if marker in visited:
                return
            visited.add(marker)
            nodes += 1
            try:
                if isinstance(value, Mapping):
                    try:
                        iterator = iter(value.items())
                    except BaseException:
                        return
                    for pair in iterator:
                        # Mapping entries are bounded by the shared node
                        # budget, not the list-item limit.  The latter is a
                        # JSON-list constraint; applying it to provider
                        # mappings silently skipped credentials after the
                        # 256th key even when the complete config was within
                        # MAX_CONFIG_NODES.
                        if nodes >= MAX_CONFIG_NODES:
                            break
                        try:
                            key, item = pair
                        except (TypeError, ValueError):
                            continue
                        try:
                            child_secret_scope = secret_scope or is_sensitive_config_key(
                                key, include_destinations=True
                            )
                        except BaseException:
                            child_secret_scope = secret_scope
                        walk(item, child_secret_scope, depth + 1)
                else:
                    try:
                        iterator = iter(value)
                    except BaseException:
                        return
                    for index, item in enumerate(iterator):
                        if index >= MAX_CONFIG_LIST_LENGTH or nodes >= MAX_CONFIG_NODES:
                            break
                        walk(item, secret_scope, depth + 1)
            except BaseException:
                return
            finally:
                visited.discard(marker)

    walk(config)
    return values


def _notification_secret_values(config: Any) -> list[str]:
    """Collect provider values that must not escape a task context.

    Notification settings are intentionally provider-agnostic, so their
    secret fields are not limited to one fixed schema.  In addition to the
    usual token/key/secret names, integrations commonly call a destination a
    URL, endpoint, webhook, or chat ID.  Walk nested provider/alias mappings
    and collect scalar values under those names so a runner can safely report
    arbitrary notification payloads.
    """

    return _config_secret_values(config)


def _secret_values(
    auth: AccountAuth,
    answer: Any,
    ocr_config: Mapping[str, Any] | None = None,
    notification_config: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    values: list[str] = []
    password = getattr(auth, "password", None)
    if password:
        values.append(str(password))
    cookies = getattr(auth, "cookies", {})
    if isinstance(cookies, Mapping):
        # Treat the cookie jar as an already-sensitive scope, while retaining
        # the same cycle/depth/item limits as provider configuration.
        values.extend(_config_secret_values({"cookies": cookies}))
    api_key = getattr(answer, "api_key", None)
    if api_key:
        values.append(str(api_key))
    values.extend(_config_secret_values(ocr_config))
    values.extend(_notification_secret_values(notification_config))
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
    return sanitize_log_message(value, secrets=secrets)


def _redact(value: Any, secrets: tuple[str, ...], *, key: Any = None) -> Any:
    """Copy and redact known secrets from reporter-owned nested values."""

    key_name = str(key).strip().lower() if key is not None else ""
    if key_name in _SENSITIVE_KEYS or any(
        marker in key_name for marker in ("password", "cookie", "api_key", "token", "secret")
    ):
        return "[redacted]"
    return sanitize_log_extra(value, secrets=secrets)


def _timestamp(value: Any = None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        try:
            value = value.timestamp()
        except (OverflowError, OSError, ValueError):
            return time.time()
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return time.time()
    return timestamp if math.isfinite(timestamp) else time.time()


def _optional_timestamp(value: Any) -> float | None:
    """Coerce a persisted timestamp without inventing one for missing data."""

    if value is None:
        return None
    if isinstance(value, datetime):
        try:
            value = value.timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) else None


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
    # Wall-clock moment at which the worker's terminal state became final.
    # Enqueued Loguru records carry their own creation timestamp; the sink may
    # still admit those records after this point, but never records created
    # later.  This closes the enqueue/terminal transition race without
    # reopening a task for arbitrary direct ``append_log`` calls.
    terminal_cutoff: float | None = None
    record_dirty: bool = False
    # Set while restoring an old record when one or more persisted messages
    # were replaced by the historical safety marker.  It is intentionally
    # internal: API callers only see the cleaned log entries.
    logs_dirty: bool = False


class TaskReporter:
    """Thread-safe progress/detail/log update facade for one task."""

    def __init__(self, manager: "TaskManager", task_id: str):
        self._manager = manager
        self.task_id = validate_task_id(task_id)

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
        terminal_task_capacity: int = DEFAULT_TERMINAL_TASK_CAPACITY,
        persistence: Any | None = None,
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
        if (
            isinstance(terminal_task_capacity, bool)
            or not isinstance(terminal_task_capacity, int)
            or terminal_task_capacity < 1
        ):
            raise ValueError("terminal_task_capacity must be a positive integer")
        self.runner = runner
        self.max_active_accounts = max_active_accounts
        self.answer_semaphore = answer_semaphore
        self.log_capacity = log_capacity
        self.terminal_task_capacity = terminal_task_capacity
        self.persistence = persistence
        self._lock = threading.RLock()
        # Keep this name public-ish: account routes use the manager as their
        # active-task guard, and diagnostics benefit from a simple mapping.
        self.active_by_account: dict[str, str] = {}
        self._tasks: dict[str, _TaskRuntime] = {}
        self._active_slots = threading.BoundedSemaphore(max_active_accounts)
        self._restore_persisted_tasks()

    @property
    def lock(self) -> threading.RLock:
        """Expose the manager lock for carefully scoped integrations."""

        return self._lock

    @property
    def semaphore(self) -> threading.BoundedSemaphore:
        return self._active_slots

    @staticmethod
    def _snapshot_mapping(snapshot: TaskSnapshot) -> dict[str, Any]:
        """Return only the public fields that may cross the store boundary."""

        return {
            "id": str(snapshot.id),
            "account_id": str(snapshot.account_id),
            "state": snapshot.state,
            "progress": _json_compatible(_copy(snapshot.progress)),
            "total": _json_compatible(_copy(snapshot.total)),
            "current_course": _json_compatible(_copy(snapshot.current_course)),
            "current_chapter": _json_compatible(_copy(snapshot.current_chapter)),
            "current_task": _json_compatible(_copy(snapshot.current_task)),
            "error": _json_compatible(_copy(snapshot.error)),
            "started_at": _json_compatible(_copy(snapshot.started_at)),
            "finished_at": _json_compatible(_copy(snapshot.finished_at)),
            "stats": _json_compatible(_copy(snapshot.stats)),
        }

    @staticmethod
    def _details_mapping(details: TaskDetails) -> dict[str, Any]:
        """Return a detached, credential-free task-details mapping."""

        return {
            "courses": _json_compatible(_copy(details.courses)),
            "active_jobs": _json_compatible(_copy(details.active_jobs)),
            "counts": _json_compatible(_copy(details.counts)),
        }

    @staticmethod
    def _safe_snapshot_value(value: Any) -> Any:
        """Return a bounded, detached public monitor value."""

        return sanitize_log_extra(value)

    @classmethod
    def _safe_snapshot_mapping(cls, value: Any) -> dict[str, Any]:
        sanitized = cls._safe_snapshot_value(value)
        return dict(sanitized) if isinstance(sanitized, Mapping) else {}

    @classmethod
    def _safe_snapshot_mapping_with_dirty(
        cls, value: Any
    ) -> tuple[dict[str, Any], bool]:
        """Sanitize a restored mapping and conservatively track rewrites."""

        sanitized = cls._safe_snapshot_mapping(value)
        if not isinstance(value, Mapping):
            return sanitized, True
        try:
            return sanitized, sanitized != value
        except BaseException:
            return sanitized, True

    @classmethod
    def _safe_details_mapping(cls, value: Any) -> dict[str, Any]:
        sanitized = cls._safe_snapshot_value(value)
        return dict(sanitized) if isinstance(sanitized, Mapping) else {}

    @staticmethod
    def _log_mapping(entry: TaskLogEntry) -> dict[str, Any]:
        return {
            "sequence": int(entry.sequence),
            "level": str(entry.level),
            "message": str(entry.message),
            "timestamp": float(entry.timestamp),
        }

    def _persist_record_locked(self, record: _TaskRuntime) -> bool:
        """Best-effort persistence of one public snapshot/details pair.

        Persistence is deliberately an adapter rather than a hard dependency
        so the process-local manager remains useful in command-line and test
        integrations.  A storage outage must not make a worker fail while it
        is reporting progress, and the adapter receives freshly allocated
        public mappings on every call.
        """

        try:
            saver = getattr(self.persistence, "save_web_task", None)
            if not callable(saver):
                return False
            result = saver(
                self._snapshot_mapping(record.snapshot),
                self._details_mapping(record.details),
            )
            persisted = result is True
            record.record_dirty = not persisted
            return persisted
        except BaseException:
            # Monitoring persistence is best effort.  In particular, an
            # injected store may not have an account row for a direct unit
            # test, while the in-memory task should still run normally.
            record.record_dirty = True
            return False

    def _persist_log_locked(self, task_id: str, entry: TaskLogEntry) -> bool:
        try:
            saver = getattr(self.persistence, "save_web_task_log", None)
            if not callable(saver):
                return False
            result = saver(
                str(task_id),
                self._log_mapping(entry),
                capacity=self.log_capacity,
            )
            # Persistence adapters must explicitly acknowledge a durable
            # write.  Treating ``None`` as success would silently clear the
            # retry/dirty path for adapters whose method failed to return.
            return result is True
        except BaseException:
            return False

    def _delete_persisted_tasks(self, task_ids: list[str]) -> None:
        if not task_ids:
            return
        try:
            deleter = getattr(self.persistence, "delete_web_tasks", None)
            if callable(deleter):
                deleter(list(task_ids))
        except Exception:
            return

    @staticmethod
    def _restore_details(value: Any) -> TaskDetails:
        value = TaskManager._safe_details_mapping(value)
        courses = value.get("courses", [])
        if not isinstance(courses, list):
            courses = list(courses) if isinstance(courses, (tuple, set)) else []
        active_jobs = value.get("active_jobs", {})
        if not isinstance(active_jobs, Mapping):
            active_jobs = {}
        counts = value.get("counts", {})
        if not isinstance(counts, Mapping):
            counts = {}
        return TaskDetails(
            courses=_copy(courses),
            active_jobs=_copy(dict(active_jobs)),
            counts=_copy(dict(counts)),
        )

    def _decode_logs(
        self, value: Any, *, ordered: bool = False
    ) -> tuple[deque[TaskLogEntry], int, bool]:
        """Restore persisted logs and mark rows rewritten for safety.

        Older releases persisted free-form question/answer/card content.  A
        restored row is cleaned as a whole when it matches one of those known
        formats, while ordinary progress metadata remains available.  The
        dirty flag allows the caller to write the cleaned rows back without
        dropping unrelated safe history.
        """

        by_sequence: dict[int, TaskLogEntry] = {}
        sequence_heap: list[int] = []
        dirty = False
        next_sequence = 0

        def decode_one(raw_entry: Any) -> None:
            nonlocal dirty, next_sequence
            if not isinstance(raw_entry, Mapping):
                return
            try:
                raw_sequence = raw_entry.get("sequence")
                if isinstance(raw_sequence, bool):
                    return
                sequence = int(raw_sequence)
            except BaseException:
                return
            if sequence < 1:
                return
            # Cursor advancement follows the highest legal sequence observed,
            # even if another field on that entry is malformed.  Otherwise a
            # later append could reuse a sequence that was visible at restore.
            next_sequence = max(next_sequence, sequence)
            try:
                timestamp = _optional_timestamp(raw_entry.get("timestamp"))
                if timestamp is None:
                    timestamp = time.time()
                level = raw_entry.get("level", "info")
                message = raw_entry.get("message", "")
                cleaned_message = sanitize_log_message(
                    message,
                    historical=True,
                )
                # Compare sanitized forms rather than calling ``str`` on a
                # dict-like/arbitrary old value: a hostile ``__str__`` must
                # not abort startup or enter a persistence/API response.
                current_message = sanitize_log_message(message)
                if cleaned_message != current_message:
                    dirty = True
            except BaseException:
                return
            entry = TaskLogEntry(
                sequence=sequence,
                level=sanitize_log_level(level),
                message=cleaned_message,
                timestamp=timestamp,
            )
            if sequence in by_sequence:
                by_sequence[sequence] = entry
                return
            if len(by_sequence) < self.log_capacity:
                by_sequence[sequence] = entry
                heapq.heappush(sequence_heap, sequence)
                return
            if sequence <= sequence_heap[0]:
                return
            evicted = heapq.heapreplace(sequence_heap, sequence)
            by_sequence.pop(evicted, None)
            by_sequence[sequence] = entry

        if ordered:
            # SQLiteStore's query is ORDER BY sequence DESC with LIMIT, then
            # reverses those rows for the public ascending log history.  A
            # tail slice is therefore safe and avoids sorting a large durable
            # history in Python.  The loader marks only this trusted path;
            # custom adapters below are never assumed to be ordered.
            try:
                selected = value[-self.log_capacity :]
            except BaseException:
                selected = ()
            try:
                selected_length = min(len(selected), self.log_capacity)
            except BaseException:
                selected_length = 0
            for index in range(selected_length):
                try:
                    decode_one(selected[index])
                except BaseException:
                    break
        elif isinstance(value, (list, tuple)):
            # Custom lists may be finite but unsorted.  Indexing avoids a
            # hostile subclass's unbounded __iter__, while the explicit scan
            # budget keeps CPU/memory bounded for oversized values.
            try:
                selected_length = min(len(value), _MAX_CUSTOM_LOG_SCAN)
            except BaseException:
                selected_length = 0
            for index in range(selected_length):
                try:
                    decode_one(value[index])
                except BaseException:
                    break
        else:
            # Arbitrary custom iterables have no reliable notion of "latest".
            # Consume at most the same finite budget and retain the highest
            # legal sequences seen, rather than trusting order or traversing
            # an infinite/malicious iterator.
            try:
                iterator = iter(value)
            except BaseException:
                iterator = iter(())
            for _ in range(_MAX_CUSTOM_LOG_SCAN):
                try:
                    raw_entry = next(iterator)
                except StopIteration:
                    break
                except BaseException:
                    break
                decode_one(raw_entry)
        ordered = sorted(by_sequence.values(), key=lambda item: item.sequence)
        return deque(ordered, maxlen=self.log_capacity), next_sequence, dirty

    def _restore_logs(self, value: Any) -> tuple[deque[TaskLogEntry], int]:
        """Backward-compatible two-value wrapper around :meth:`_decode_logs`."""

        logs, next_sequence, _ = self._decode_logs(value)
        return logs, next_sequence

    def _restore_record(
        self,
        payload: Any,
    ) -> tuple[_TaskRuntime, bool] | None:
        """Build a terminal runtime from a persistence adapter response.

        A restored runtime has no worker thread and an empty credential
        context.  The second return value indicates that an interrupted task
        was converted from ``running``/``stopping`` to ``failed`` and should
        be written back in its normalized form.
        """

        if not isinstance(payload, Mapping):
            return None
        raw_snapshot = payload.get("snapshot")
        if not isinstance(raw_snapshot, Mapping):
            return None
        # Persistence adapters are an untrusted boundary.  Do this before
        # reading any nested public field so a custom adapter (or a legacy
        # SQLite row) cannot reintroduce credentials, cycles, or oversized
        # structures into the monitor API.
        raw_snapshot, record_dirty = self._safe_snapshot_mapping_with_dirty(
            raw_snapshot
        )
        record_dirty = record_dirty or payload.get("_record_dirty") is True
        try:
            task_id = validate_task_id(raw_snapshot.get("id"))
        except (TypeError, ValueError):
            return None
        account_id = str(raw_snapshot.get("account_id", "")).strip()
        if not task_id or not account_id:
            return None
        state = raw_snapshot.get("state")
        if not isinstance(state, str) or state not in {
            "running",
            "stopping",
            "completed",
            "failed",
            "stopped",
        }:
            return None
        interrupted = state in {"running", "stopping"}
        if interrupted:
            state = "failed"
        stats = raw_snapshot.get("stats", {})
        if not isinstance(stats, Mapping):
            stats = {}
        error = raw_snapshot.get("error")
        if interrupted:
            error = _RESTART_INTERRUPTION_ERROR
        elif error is not None:
            cleaned_error = sanitize_log_message(error, historical=True)
            if cleaned_error != error:
                record_dirty = True
            error = cleaned_error
        started_at = _optional_timestamp(raw_snapshot.get("started_at"))
        finished_at = _optional_timestamp(raw_snapshot.get("finished_at"))
        if interrupted and finished_at is None:
            finished_at = time.time()
        snapshot = TaskSnapshot(
            id=task_id,
            account_id=account_id,
            state=state,
            progress=_copy(raw_snapshot.get("progress", 0)),
            total=_copy(raw_snapshot.get("total", 0)),
            current_course=_copy(raw_snapshot.get("current_course")),
            current_chapter=_copy(raw_snapshot.get("current_chapter")),
            current_task=_copy(raw_snapshot.get("current_task")),
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            stats=_copy(dict(stats)),
        )
        raw_details = payload.get("details", {})
        details_mapping, details_dirty = self._safe_snapshot_mapping_with_dirty(
            raw_details
        )
        record_dirty = record_dirty or details_dirty
        details = self._restore_details(details_mapping)
        if interrupted:
            # The old worker cannot still be running after this process has
            # started, so stale active jobs would be misleading in the
            # terminal monitor view.
            details = replace(details, active_jobs={})
        logs, next_sequence, logs_dirty = self._decode_logs(
            payload.get("logs", []),
            ordered=payload.get("_logs_ordered") is True,
        )
        # SQLiteStore sanitizes rows while keeping its loader pure-read.  Its
        # private marker tells this manager that the in-memory canonical value
        # still needs a separate best-effort write-back.
        logs_dirty = logs_dirty or payload.get("_logs_dirty") is True
        if interrupted:
            next_sequence += 1
            interruption_entry = TaskLogEntry(
                sequence=next_sequence,
                level="warning",
                message=_RESTART_INTERRUPTION_ERROR,
                timestamp=finished_at if finished_at is not None else time.time(),
            )
            logs.append(interruption_entry)
        cancel_event = threading.Event()
        done = threading.Event()
        done.set()
        started = threading.Event()
        started.set()
        context = StudyRunContext(
            task_id=task_id,
            account_id=account_id,
            course_ids=[],
            preferences=AccountPreferences(),
            auth=AccountAuth(username="", password="", cookies={}),
            answer=None,
            answer_semaphore=None,
            cancel_event=cancel_event,
            reporter=TaskReporter(self, task_id),
        )
        return (
            _TaskRuntime(
                snapshot=snapshot,
                details=details,
                context=context,
                done=done,
                started=started,
                logs=logs,
                next_sequence=next_sequence,
                slot_released=True,
                record_dirty=record_dirty,
                logs_dirty=logs_dirty,
            ),
            interrupted,
        )

    def _restore_persisted_tasks(self) -> None:
        # Prune while stale rows still advertise running/stopping.  The store
        # deliberately preserves those rows, so a small terminal quota cannot
        # discard them before this manager has a chance to normalize each one
        # to the restart-interrupted terminal state.
        self._prune_persisted_history()
        try:
            loader = getattr(self.persistence, "load_web_tasks", None)
            if not callable(loader):
                return
            try:
                payloads = loader(
                    limit=self.terminal_task_capacity,
                    log_capacity=self.log_capacity,
                )
            except TypeError:
                # Keep compatibility with small in-memory adapters written
                # against the original ``limit``-only protocol.
                payloads = loader(limit=self.terminal_task_capacity)
            payloads = list(payloads)
        except Exception:
            self._prune_persisted_history()
            return
        with self._lock:
            interrupted_ids: set[str] = set()
            for payload in payloads:
                restored = self._restore_record(payload)
                if restored is None:
                    continue
                record, interrupted = restored
                self._tasks[record.snapshot.id] = record
                if interrupted:
                    interrupted_ids.add(record.snapshot.id)
                if record.record_dirty:
                    self._persist_record_locked(record)
                self._persist_dirty_logs_locked(record)
                if interrupted:
                    if not self._persist_log_locked(record.snapshot.id, record.logs[-1]):
                        record.logs_dirty = True
                    self._persist_record_locked(record)
            # Every stale active row was explicitly requested by the loader;
            # keep those normalized interruption records for this startup even
            # when the terminal quota is one.  Future terminal transitions use
            # the ordinary bounded history path.
            self._trim_terminal_locked(preserve_ids=interrupted_ids)

    def _prune_persisted_history(self) -> bool:
        """Ask the persistence adapter to trim durable monitor history."""

        if self.persistence is None:
            return False
        try:
            pruner = getattr(self.persistence, "prune_web_tasks", None)
            if not callable(pruner):
                return False
            result = pruner(
                log_capacity=self.log_capacity,
                terminal_task_capacity=self.terminal_task_capacity,
            )
            return result is True
        except BaseException:
            return False

    def _persist_dirty_logs_locked(self, record: _TaskRuntime) -> bool:
        """Best-effort historical migration with an honest dirty flag."""

        if not record.logs_dirty:
            return True
        all_saved = True
        for entry in record.logs:
            if not self._persist_log_locked(record.snapshot.id, entry):
                all_saved = False
        if all_saved:
            record.logs_dirty = False
        return all_saved

    @staticmethod
    def _history_timestamp(record: _TaskRuntime) -> float:
        value = record.snapshot.finished_at
        if value is None:
            value = record.snapshot.started_at
        timestamp = _optional_timestamp(value)
        if timestamp is None:
            return 0.0
        return timestamp

    @staticmethod
    def _list_timestamp(record: _TaskRuntime) -> float:
        value = record.snapshot.finished_at
        if value is None:
            value = record.snapshot.started_at
        timestamp = _optional_timestamp(value)
        if timestamp is None:
            return 0.0
        return timestamp

    def _trim_terminal_locked(
        self, *, preserve_ids: set[str] | frozenset[str] = frozenset()
    ) -> None:
        terminal = [
            item
            for item in self._tasks.values()
            if item.snapshot.state not in {"running", "stopping"}
            and item.snapshot.id not in preserve_ids
        ]
        terminal.sort(
            key=lambda item: (self._history_timestamp(item), item.snapshot.id)
        )
        stale = terminal[: -self.terminal_task_capacity]
        if not stale:
            return
        stale_ids = [item.snapshot.id for item in stale]
        for task_id in stale_ids:
            self._tasks.pop(task_id, None)
        self._delete_persisted_tasks(stale_ids)

    def set_max_active_accounts(self, value: int) -> None:
        """Apply a new active-account limit at an idle settings boundary.

        The HTTP settings route rejects mutations while tasks are active, so
        replacing the semaphore here cannot strand an in-flight permit.  The
        lock still makes direct integrations deterministic and keeps a future
        caller from racing a start operation.
        """

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 10
        ):
            raise ValueError("max_active_accounts must be between 1 and 10")
        with self._lock:
            if self.active_by_account:
                raise TaskManagerError(
                    "cannot change active-account limit while tasks are running"
                )
            self.max_active_accounts = value
            self._active_slots = threading.BoundedSemaphore(value)

    def _lookup(self, task_id: str) -> _TaskRuntime:
        try:
            task_id = validate_task_id(task_id)
        except (TypeError, ValueError):
            raise TaskNotFound("invalid task id") from None
        record = self._tasks.get(task_id)
        if record is None:
            raise TaskNotFound(task_id)
        return record

    def _secrets_for(self, record: _TaskRuntime) -> tuple[str, ...]:
        return _secret_values(
            record.context.auth,
            record.context.answer,
            record.context.preferences.ocr_config,
            record.context.preferences.notification_config,
        )

    def start(
        self,
        account_id: str,
        course_ids: list[str],
        preferences: AccountPreferences,
        auth: AccountAuth,
        answer: ResolvedAnswerConnection | None = None,
        *,
        answer_semaphore: threading.Semaphore | None | object = _UNSET,
        wait_started: bool = True,
    ) -> TaskSnapshot:
        """Admit and asynchronously start one account-owned study task."""

        account = str(account_id)
        if not account.strip():
            raise ValueError("account_id must not be blank")
        if not isinstance(course_ids, list):
            raise TypeError("course_ids must be a list")
        if not course_ids:
            raise ValueError("course_ids must not be empty")
        if len(course_ids) > MAX_SELECTED_COURSE_IDS:
            raise ValueError("too many course_ids")
        if not all(
            isinstance(item, str)
            and item.strip()
            and len(item.strip()) <= MAX_COURSE_ID_LENGTH
            for item in course_ids
        ):
            raise ValueError("course_ids contain an invalid value")
        # Resolve and copy all caller-owned values before acquiring a global
        # slot.  Invalid input must not strand a semaphore permit.
        copied_course_ids = list(dict.fromkeys(item.strip() for item in course_ids))
        copied_preferences = _account_preferences(preferences)
        copied_auth = _account_auth(auth)
        copied_answer = _answer_connection(answer)
        task_answer_semaphore = (
            self.answer_semaphore
            if answer_semaphore is _UNSET
            else answer_semaphore
        )

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
                answer_semaphore=task_answer_semaphore,
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
            # Persist the initial running state before handing the worker its
            # context.  The mapping contains no auth/preferences/answer
            # values, and a later restart can normalize this state to a
            # terminal failure if the process disappears.
            self._persist_record_locked(record)

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
        # waiting for the runner itself (which may intentionally block).  The
        # HTTP route can defer this wait until after releasing the shared
        # account/store coordination lock.
        if wait_started:
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
            run_with_task_context(
                task_id,
                self._invoke_runner,
                context,
                log_secrets=self._secrets_for(record),
            )
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
            # Loguru's enqueue worker may still be draining records emitted
            # immediately before the runner returned.  Drain after releasing
            # the manager lock: the route callback needs that lock, and taking
            # it while waiting for ``logger.complete`` would deadlock.
            from .task_logging import complete_task_log_sink

            complete_task_log_sink()

    def _sanitize(self, record: _TaskRuntime, value: Any) -> str:
        text = sanitize_log_message(
            value,
            secrets=self._secrets_for(record),
            historical=True,
        )
        return text or "Task failed"

    @staticmethod
    def _snapshot_copy(snapshot: TaskSnapshot) -> TaskSnapshot:
        return replace(
            snapshot,
            progress=_json_compatible(_copy(snapshot.progress)),
            total=_json_compatible(_copy(snapshot.total)),
            current_course=_json_compatible(_copy(snapshot.current_course)),
            current_chapter=_json_compatible(_copy(snapshot.current_chapter)),
            current_task=_json_compatible(_copy(snapshot.current_task)),
            error=sanitize_log_message(snapshot.error, historical=True)
            if snapshot.error is not None
            else None,
            stats=_json_compatible(_copy(snapshot.stats)),
        )

    @staticmethod
    def _details_copy(details: TaskDetails) -> TaskDetails:
        return TaskDetails(
            courses=_json_compatible(_copy(details.courses)),
            active_jobs=_json_compatible(_copy(details.active_jobs)),
            counts=_json_compatible(_copy(details.counts)),
        )

    def _finish_locked(
        self,
        record: _TaskRuntime,
        state: TaskState,
        error: str | None,
    ) -> None:
        if record.done.is_set():
            return
        # Capture the terminal boundary before changing the public state.  A
        # late enqueue callback can then distinguish records generated before
        # return from a genuinely post-terminal logger call.
        record.terminal_cutoff = time.time()
        record.snapshot = replace(
            record.snapshot,
            state=state,
            error=error,
            finished_at=time.time(),
            stats=_copy(record.snapshot.stats),
        )
        # A cancellation or worker error can bypass the normal job-done
        # callback.  Never leave a terminal task advertising phantom active
        # jobs to the monitor.
        record.details = replace(record.details, active_jobs={})
        if self.active_by_account.get(record.snapshot.account_id) == record.snapshot.id:
            self.active_by_account.pop(record.snapshot.account_id, None)
        if not record.slot_released:
            record.slot_released = True
            self._active_slots.release()
        # The runner has unwound and no longer needs decrypted credentials.
        # Retain only non-secret metadata needed by the monitor UI.
        record.context = replace(
            record.context,
            auth=AccountAuth(
                username="",
                password="",
                cookies={},
                auth_mode=record.context.auth.auth_mode,
            ),
            answer=(
                replace(record.context.answer, api_key=None, has_api_key=False)
                if record.context.answer is not None
                else None
            ),
            preferences=replace(
                record.context.preferences,
                notification_config={},
                ocr_config={},
            ),
            answer_semaphore=None,
        )
        record.done.set()
        self._persist_record_locked(record)
        self._trim_terminal_locked()

    def cancel(self, task_id: str) -> TaskSnapshot:
        """Request cooperative cancellation and return the new snapshot."""

        with self._lock:
            record = self._lookup(task_id)
            if record.snapshot.state not in {"running", "stopping"}:
                raise TaskNotRunning(str(task_id))
            if record.snapshot.state == "running":
                record.snapshot = replace(record.snapshot, state="stopping")
            record.context.cancel_event.set()
            self._persist_record_locked(record)
            return self._snapshot_copy(record.snapshot)

    def wait(self, task_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            record = self._lookup(task_id)
        # ``done`` is set at the terminal state transition.  Do not turn this
        # bounded wait into an unbounded Loguru queue drain: a temporarily
        # blocked sink must not change the timeout contract.  The worker's
        # post-finalization drain and ``get_logs``'s explicit drain provide the
        # eventual queue completion independently.
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
            for record in self._tasks.values():
                if record.record_dirty:
                    self._persist_record_locked(record)
            records = sorted(
                self._tasks.values(),
                key=lambda item: (self._list_timestamp(item), item.snapshot.id),
                reverse=True,
            )
            return [self._snapshot_copy(item.snapshot) for item in records]

    def get_snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            record = self._lookup(task_id)
            if record.record_dirty:
                self._persist_record_locked(record)
            return self._snapshot_copy(record.snapshot)

    def get_details(self, task_id: str) -> TaskDetails:
        with self._lock:
            record = self._lookup(task_id)
            if record.record_dirty:
                self._persist_record_locked(record)
            return self._details_copy(record.details)

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
            self._persist_record_locked(record)

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
            self._persist_record_locked(record)

    def _set_courses(self, task_id: str, courses: Any) -> None:
        with self._lock:
            record = self._lookup(task_id)
            value = [] if courses is None else courses
            copied = _redact(value, self._secrets_for(record))
            if not isinstance(copied, list):
                copied = list(copied) if isinstance(copied, (tuple, set)) else [copied]
            record.details = replace(record.details, courses=copied)
            self._persist_record_locked(record)

    def _set_active_jobs(self, task_id: str, active_jobs: Any) -> None:
        with self._lock:
            record = self._lookup(task_id)
            value = {} if active_jobs is None else active_jobs
            copied = _redact(value, self._secrets_for(record))
            if not isinstance(copied, dict):
                copied = {}
            record.details = replace(record.details, active_jobs=copied)
            self._persist_record_locked(record)

    def append_log(
        self,
        task_id: str,
        message: Any,
        level: str = "info",
        timestamp: Any = None,
        *,
        _event_time: Any = None,
    ) -> TaskLogEntry | None:
        """Append one redacted entry, preserving per-task order.

        Unknown task IDs are ignored.  This is important for the global
        Loguru sink: a late queued record from a task that has already been
        torn down must never turn logging itself into an application error.

        ``_event_time`` is retained as a compatibility-shaped keyword for
        callers that may have discovered an older private implementation, but
        it is never trusted.  Only :meth:`_append_log_from_sink`, guarded by
        the private capability held by ``task_logging``, can admit a queued
        record after terminalization.
        """

        if _event_time is not None:
            return None
        return self._append_log_internal(
            task_id,
            message,
            level,
            timestamp=timestamp,
            event_time=None,
            from_sink=False,
        )

    def _append_log_from_sink(
        self,
        task_id: str,
        message: Any,
        level: str = "info",
        *,
        timestamp: Any = None,
        _capability: Any = None,
    ) -> TaskLogEntry | None:
        """Admit one genuine queued Loguru record.

        This method is intentionally private and requires the identity of a
        capability object created and retained only by ``task_logging``.  A
        caller can pass any timestamp to the public API, but it cannot turn
        that value into a trusted pre-terminal event marker.
        """

        from .task_logging import _TASK_LOG_SINK_CAPABILITY

        if _capability is not _TASK_LOG_SINK_CAPABILITY:
            return None
        # ``None`` is supported only for tiny synthetic records that omit
        # Loguru's ``time`` field; active records receive a fresh finite local
        # timestamp, while terminal records are rejected below.  An explicit
        # malformed value is never normalized into a trusted event time.
        event_time = _optional_timestamp(timestamp)
        if timestamp is not None and event_time is None:
            return None
        return self._append_log_internal(
            task_id,
            message,
            level,
            timestamp=event_time,
            event_time=event_time,
            from_sink=True,
        )

    def _append_log_internal(
        self,
        task_id: str,
        message: Any,
        level: str,
        *,
        timestamp: Any,
        event_time: float | None,
        from_sink: bool,
    ) -> TaskLogEntry | None:
        """Append after the caller's provenance has been established."""

        with self._lock:
            try:
                task_id = validate_task_id(task_id)
            except (TypeError, ValueError):
                return None
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.snapshot.state not in {"running", "stopping"}:
                if (
                    not from_sink
                    or event_time is None
                    or record.terminal_cutoff is None
                    or event_time > record.terminal_cutoff
                ):
                    return None
            record.next_sequence += 1
            entry = TaskLogEntry(
                sequence=record.next_sequence,
                level=sanitize_log_level(level),
                message=sanitize_log_message(
                    message,
                    secrets=self._secrets_for(record),
                    historical=True,
                ),
                timestamp=_timestamp(timestamp),
            )
            record.logs.append(entry)
            persisted = self._persist_log_locked(record.snapshot.id, entry)
            if self.persistence is not None and not persisted:
                record.logs_dirty = True
            return entry

    def get_logs(self, task_id: str, after: int = 0) -> TaskLogPage:
        """Read task logs without consuming or mutating the buffer."""

        if after is None:
            after = 0
        if isinstance(after, bool) or not isinstance(after, int):
            raise ValueError("after cursor must be a non-negative integer")
        if after < 0:
            raise ValueError("after cursor must be a non-negative integer")
        from .task_logging import complete_task_log_sink

        complete_task_log_sink()
        with self._lock:
            record = self._lookup(task_id)
            if record.record_dirty:
                self._persist_record_locked(record)
            self._persist_dirty_logs_locked(record)
            secrets = self._secrets_for(record)
            items: list[TaskLogEntry] = []
            for index, item in enumerate(record.logs):
                if item.sequence <= after:
                    continue
                # The route is a security boundary.  Re-sanitize here even
                # though append/restore already clean entries, so a legacy
                # adapter or an in-process integration cannot reintroduce a
                # sensitive row after startup.
                cleaned = sanitize_log_message(
                    item.message,
                    secrets=secrets,
                    historical=True,
                )
                if cleaned != item.message:
                    replacement = replace(item, message=cleaned)
                    try:
                        record.logs[index] = replacement
                    except (ValueError, IndexError):
                        # A concurrent teardown cannot invalidate the API
                        # response; return the detached replacement regardless.
                        pass
                    item = replacement
                    if self.persistence is not None and not self._persist_log_locked(
                        record.snapshot.id, replacement
                    ):
                        # A sanitized replacement is still a migration.  Do
                        # not claim it was durable until the adapter confirms
                        # the write, otherwise a later restart can restore the
                        # old unsafe value without another retry.
                        record.logs_dirty = True
                items.append(item)
            next_cursor = max(after, record.next_sequence)
            return TaskLogPage(items=_copy(items), next_cursor=next_cursor)


# Re-exporting the helper keeps the task-facing API convenient while the
# implementation remains in the dedicated logging module.
from .task_logging import run_with_task_context


__all__ = [
    "AccountTaskConflict",
    "DEFAULT_LOG_CAPACITY",
    "DEFAULT_TERMINAL_TASK_CAPACITY",
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
