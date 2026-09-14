"""Task-scoped Loguru context and the process-wide routing sink."""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING, Any, Callable

from api.logger import logger

if TYPE_CHECKING:  # pragma: no cover - imports used only by type checkers
    from .task_manager import TaskManager


_SINK_LOCK = threading.RLock()
_SINK_ID: int | None = None
_MANAGERS: weakref.WeakSet["TaskManager"] = weakref.WeakSet()


def run_with_task_context(
    task_id: str,
    target: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run ``target`` with a Loguru ``task_id`` context value."""

    with logger.contextualize(task_id=str(task_id)):
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


def _route_record(message: Any) -> None:
    record = getattr(message, "record", {})
    extra = record.get("extra", {}) if isinstance(record, dict) else {}
    task_id = extra.get("task_id") if isinstance(extra, dict) else None
    if not task_id:
        return
    text = record.get("message", "") if isinstance(record, dict) else ""
    level = record.get("level") if isinstance(record, dict) else None
    level_name = getattr(level, "name", level or "info")
    timestamp = record.get("time") if isinstance(record, dict) else None
    timestamp_value = (
        timestamp.timestamp() if hasattr(timestamp, "timestamp") else timestamp
    )

    # A task ID is globally unique, so exactly one manager should accept the
    # record.  Iterating a weak set snapshot keeps callbacks safe if app/test
    # teardown drops a manager while Loguru's enqueue worker is draining.
    with _SINK_LOCK:
        managers = tuple(_MANAGERS)
    for manager in managers:
        try:
            entry = manager.append_log(
                str(task_id),
                text,
                str(level_name).lower(),
                timestamp=timestamp_value,
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
                filter=lambda record: bool(record["extra"].get("task_id")),
                enqueue=True,
            )
        _MANAGERS.add(manager)
        return _SINK_ID


def remove_task_log_sink(sink_id: int | None = None) -> None:
    """Remove the routing sink, primarily for deterministic test teardown."""

    global _SINK_ID
    with _SINK_LOCK:
        target = _SINK_ID if sink_id is None else sink_id
        if target is not None:
            logger.remove(target)
        if sink_id is None or sink_id == _SINK_ID:
            _SINK_ID = None
            _MANAGERS.clear()


__all__ = [
    "install_task_log_sink",
    "remove_task_log_sink",
    "run_with_task_context",
]
