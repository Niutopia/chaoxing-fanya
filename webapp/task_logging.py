"""Task-scoped Loguru context and the process-wide routing sink."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Callable

from api.logger import (
    _normalise_secrets,
    logger,
    validate_task_id,
)

if TYPE_CHECKING:  # pragma: no cover - imports used only by type checkers
    from .task_manager import TaskManager


_SINK_LOCK = threading.RLock()
# ``logger.complete()`` waits for Loguru's enqueue worker.  Serializing drains
# keeps concurrent fast workers from contending in Loguru's private handler
# bookkeeping, while deliberately using a different lock from ``_SINK_LOCK``
# so a sink callback can always publish its manager snapshot during a drain.
_SINK_COMPLETE_LOCK = threading.Lock()
_SINK_ID: int | None = None
_MANAGERS: weakref.WeakSet["TaskManager"] = weakref.WeakSet()
_MANAGERS_SNAPSHOT: tuple["TaskManager", ...] = ()


def _refresh_manager_snapshot_locked() -> None:
    global _MANAGERS_SNAPSHOT
    _MANAGERS_SNAPSHOT = tuple(_MANAGERS)


def run_with_task_context(
    task_id: str,
    target: Callable[..., Any],
    *args: Any,
    log_secrets: Iterable[Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Run ``target`` with a Loguru ``task_id`` context value."""

    task_id = validate_task_id(task_id)
    secrets = _normalise_secrets(log_secrets) if log_secrets is not None else None
    # The logger patcher consumes ``_log_secrets`` before a record reaches any
    # sink.  Keeping the values in Loguru's scoped context lets console/file
    # sinks protect exceptions and direct logger calls from a task, while no
    # credential-bearing value is serialized as an ``extra`` field.
    context_values: dict[str, Any] = {"task_id": task_id}
    if secrets is not None:
        context_values["_log_secrets"] = secrets
    with logger.contextualize(**context_values):
        return target(*args, **kwargs)


def _sink_is_installed(sink_id: int | None) -> bool:
    if sink_id is None:
        return False
    # Loguru intentionally keeps handler bookkeeping private.  Checking it is
    # still preferable to adding a duplicate sink when a test teardown called
    # ``logger.remove(sink_id)`` directly, which is the common fixture pattern.
    core = getattr(logger, "_core", None)
    handlers = getattr(core, "handlers", {}) if core is not None else {}
    return sink_id in handlers


def _task_record_filter(record: dict[str, Any]) -> bool:
    """Filter only records carrying a valid, bounded task identifier."""

    try:
        validate_task_id(record.get("extra", {}).get("task_id"))
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _route_record(message: Any) -> None:
    record = getattr(message, "record", {})
    extra = record.get("extra", {}) if isinstance(record, dict) else {}
    try:
        task_id = validate_task_id(extra.get("task_id"))
    except (TypeError, ValueError):
        return
    text = record.get("message", "") if isinstance(record, dict) else ""
    level = record.get("level") if isinstance(record, dict) else None
    level_name = getattr(level, "name", level or "info")
    timestamp = record.get("time") if isinstance(record, dict) else None
    timestamp_value = (
        timestamp.timestamp() if hasattr(timestamp, "timestamp") else timestamp
    )

    # A task ID is globally unique, so exactly one manager should accept the
    # record.  The immutable snapshot is refreshed under ``_SINK_LOCK`` by
    # install/unregister operations, but read lock-free here.  This is
    # important because ``logger.complete()`` may be called by code that is
    # already holding the routing lock; taking the same lock in this callback
    # would deadlock the enqueue worker while the caller waits for it.
    managers = _MANAGERS_SNAPSHOT
    for manager in managers:
        try:
            entry = manager.append_log(
                task_id,
                text,
                str(level_name).lower(),
                timestamp=timestamp_value,
                _event_time=timestamp_value,
            )
        except Exception:
            # Logging must remain best-effort.  In particular, a manager may
            # be in teardown while a queued Loguru record is being delivered.
            continue
        if entry is not None:
            return


def install_task_log_sink(manager: "TaskManager") -> int:
    """Install/reuse one process-wide sink and register ``manager`` with it."""

    global _SINK_ID
    with _SINK_LOCK:
        if not _sink_is_installed(_SINK_ID):
            _SINK_ID = logger.add(
                _route_record,
                filter=_task_record_filter,
                enqueue=True,
            )
        _MANAGERS.add(manager)
        _refresh_manager_snapshot_locked()
        return _SINK_ID


def remove_task_log_sink(sink_id: int | None = None) -> None:
    """Remove the routing sink, primarily for deterministic test teardown."""

    global _SINK_ID
    with _SINK_LOCK:
        target = _SINK_ID if sink_id is None else sink_id
        if target is None:
            return
        should_clear = sink_id is None or sink_id == _SINK_ID
        if should_clear:
            _SINK_ID = None
            _MANAGERS.clear()
            _refresh_manager_snapshot_locked()
    # ``logger.remove`` waits for an enqueue=True sink to drain.  Never hold
    # the routing lock while waiting: the sink callback itself acquires that
    # lock to append its record, otherwise teardown can deadlock forever.
    try:
        logger.remove(target)
    except Exception:
        # Teardown is best effort; a sink may already have been removed by a
        # test fixture or by an embedding application's logger lifecycle.
        return


def unregister_task_log_sink(manager: "TaskManager") -> None:
    """Detach one manager while preserving the shared routing sink.

    Loguru owns one process-global sink, but Flask tests and embedded hosts can
    create several independent task managers.  Removing a manager must not
    drop records for the remaining managers; the underlying sink is removed
    only when the last manager leaves.
    """

    global _SINK_ID
    with _SINK_LOCK:
        _MANAGERS.discard(manager)
        _refresh_manager_snapshot_locked()
        if _MANAGERS:
            return
        target = _SINK_ID
        _SINK_ID = None
    if target is not None:
        try:
            logger.remove(target)
        except Exception:
            return


def complete_task_log_sink() -> None:
    """Drain queued task records without holding the routing lock.

    The returned awaitable from :func:`loguru.logger.complete` is only needed
    for coroutine sinks.  Its synchronous portion drains every
    ``enqueue=True`` handler, which is the part required for task monitor
    records.  A separate lock prevents a hundred workers finishing together
    from repeatedly entering Loguru's handler core while still allowing the
    callback itself to take the manager lock.
    """

    with _SINK_LOCK:
        if _SINK_ID is None:
            return
    with _SINK_COMPLETE_LOCK:
        try:
            logger.complete()
        except Exception:
            # A host application may remove/reconfigure Loguru handlers while
            # a worker is unwinding.  The timestamp cutoff remains the
            # correctness boundary even if this best-effort drain fails.
            return


__all__ = [
    "install_task_log_sink",
    "remove_task_log_sink",
    "unregister_task_log_sink",
    "complete_task_log_sink",
    "run_with_task_context",
]
