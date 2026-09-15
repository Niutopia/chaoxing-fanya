"""Task start, monitoring, log, and cancellation endpoints.

Live execution remains process-local, while credential-free monitor records
are mirrored to SQLite so an application restart does not erase task history.
Routes resolve account credentials and the shared answer connection only for
the short startup hand-off; all HTTP serializers below operate on the public
task value objects returned by the manager.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from api.logger import sanitize_log_message

from ..answer_connection import outbound_url
from ..limits import MAX_COURSE_ID_LENGTH, MAX_SELECTED_COURSE_IDS
from ..models import AccountAuth, AccountPreferences, ResolvedAnswerConnection
from ..task_manager import (
    AccountTaskConflict,
    TaskCapacityReached,
    TaskManager,
    TaskNotFound,
    TaskNotRunning,
)


tasks = Blueprint("tasks", __name__, url_prefix="/api")

_ANSWER_TEST_EXTENSION = "answer_connection_test"


def _services() -> Mapping[str, Any]:
    return current_app.extensions["services"]


def _manager() -> TaskManager:
    manager = _services().get("task_manager") or current_app.extensions.get(
        "task_manager"
    )
    if manager is None:
        raise RuntimeError("task manager is not configured")
    return manager


def _coordination_boundary(manager: Any | None = None):
    """Return the lock shared by task admission and account mutations."""

    manager = manager or _manager()
    lock = getattr(manager, "lock", None)
    if lock is None:
        lock = _services().get("account_task_lock")
    if lock is None:
        lock = current_app.extensions.get("account_task_lock")
    return lock if callable(getattr(lock, "__enter__", None)) else nullcontext()


def _error(message: str, code: str, status_code: int):
    return jsonify(status=False, msg=message, code=code), status_code


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    """Serialize a :class:`TaskSnapshot` without reflecting internals."""

    return {
        "id": snapshot.id,
        "account_id": snapshot.account_id,
        "state": snapshot.state,
        "progress": snapshot.progress,
        "total": snapshot.total,
        "current_course": snapshot.current_course,
        "current_chapter": snapshot.current_chapter,
        "current_task": snapshot.current_task,
        "error": snapshot.error,
        "started_at": snapshot.started_at,
        "finished_at": snapshot.finished_at,
        "stats": _copy_json_value(snapshot.stats),
    }


def _details_data(details: Any) -> dict[str, Any]:
    return {
        "courses": _copy_json_value(details.courses),
        "active_jobs": _copy_json_value(details.active_jobs),
        "counts": _copy_json_value(details.counts),
    }


def _log_data(entry: Any) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "level": entry.level,
        # Keep a final serializer boundary in case a custom manager/adapter
        # supplies an unclean entry.  Historical content is replaced as a
        # whole; harmless progress metadata remains visible.
        "message": sanitize_log_message(entry.message, historical=True),
        "timestamp": entry.timestamp,
    }


def _copy_json_value(value: Any) -> Any:
    """Deep-copy JSON-compatible values and tolerate test doubles."""

    if isinstance(value, Mapping):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, set):
        return [_copy_json_value(item) for item in value]
    return value


def _profile_enabled(profile: Any) -> bool:
    if isinstance(profile, Mapping):
        return bool(profile.get("enabled", False))
    return bool(getattr(profile, "enabled", False))


def _json_mapping() -> Mapping[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, Mapping) else None


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_preferences(value: Any) -> bool:
    """Validate persisted preferences at the task boundary.

    The preferences route validates writes, but the store is also an explicit
    integration seam.  Revalidating here keeps malformed persisted data from
    reaching the worker and turns it into a stable client error.
    """

    if not isinstance(value, AccountPreferences):
        return False
    selected = value.selected_course_ids
    if not isinstance(selected, list) or not all(
        isinstance(course_id, str) and course_id.strip() for course_id in selected
    ):
        return False
    if len(selected) > MAX_SELECTED_COURSE_IDS or any(
        len(course_id) > MAX_COURSE_ID_LENGTH for course_id in selected
    ):
        return False
    if not _finite_number(value.speed) or not 1.0 <= float(value.speed) <= 2.0:
        return False
    if (
        isinstance(value.jobs, bool)
        or not isinstance(value.jobs, int)
        or not 1 <= value.jobs <= 10
    ):
        return False
    if value.notopen_action not in {"retry", "continue"}:
        return False
    if not isinstance(value.answer_enabled, bool) or not isinstance(
        value.answer_auto_submit, bool
    ):
        return False
    if not _finite_number(value.answer_cover_rate) or not 0.0 <= float(
        value.answer_cover_rate
    ) <= 1.0:
        return False
    return isinstance(value.notification_config, Mapping) and isinstance(
        value.ocr_config, Mapping
    )


def _course_ids(payload: Mapping[str, Any], preferences: AccountPreferences) -> tuple[list[str], bool]:
    """Resolve request course selection and whether it must be persisted."""

    provided = "course_ids" in payload
    value = payload.get("course_ids") if provided else preferences.selected_course_ids
    if not isinstance(value, list):
        raise ValueError("courses_required")
    if not value:
        raise ValueError("courses_required")
    if len(value) > MAX_SELECTED_COURSE_IDS:
        raise ValueError("invalid_courses")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("invalid_courses")
    if any(len(item.strip()) > MAX_COURSE_ID_LENGTH for item in value):
        raise ValueError("invalid_courses")
    # De-duplicate while retaining the user's order.  Sending the same course
    # twice must not make the runner process it twice.
    result: list[str] = []
    for item in value:
        item = item.strip()
        if item not in result:
            result.append(item)
    return result, provided


def _public_answer_connection(value: Any) -> tuple[Any, ...]:
    """Return fields that identify a saved answer connection, including key."""

    api_key = str(getattr(value, "api_key", "") or "")
    has_api_key = getattr(value, "has_api_key", None)
    if has_api_key is None:
        has_api_key = bool(api_key)
    return (
        str(getattr(value, "base_url", "")),
        str(getattr(value, "model", "")),
        bool(getattr(value, "enabled", False)),
        bool(has_api_key),
        api_key,
    )


def _answer_fingerprint(value: Any) -> str:
    """Hash the key-bearing identity without retaining the key in app state."""

    parts = "\x1f".join(
        str(item) for item in _public_answer_connection(value)
    ).encode("utf-8")
    return hashlib.sha256(parts).hexdigest()


def _answer_test_state() -> dict[str, Any]:
    state = current_app.extensions.setdefault(_ANSWER_TEST_EXTENSION, {})
    if not isinstance(state, dict):
        state = {}
        current_app.extensions[_ANSWER_TEST_EXTENSION] = state
    return state


def _record_answer_test(connection: Any, *, status: str) -> None:
    """Record only status/fingerprint metadata after a connection probe."""

    state = _answer_test_state()
    state["status"] = status
    fingerprint = _answer_fingerprint(connection)
    state["fingerprint"] = fingerprint
    saver = getattr(_services().get("store"), "save_answer_test_status", None)
    if callable(saver):
        try:
            saver(status, fingerprint)
        except Exception:
            # In-memory metadata remains useful for injected stores that do
            # not support persistence; failure to persist is not a reason to
            # expose a storage exception through the test endpoint.
            pass


def invalidate_answer_test() -> None:
    """Invalidate a previously successful connection check for this app."""

    # This helper is imported by the settings blueprint.  Outside a request
    # context, invalidation is simply a no-op; normal settings mutations call
    # it while handling a request.
    try:
        state = _answer_test_state()
    except RuntimeError:
        return
    state.clear()


def _answer_test_matches(connection: Any) -> bool:
    state = _answer_test_state()
    return state.get("status") == "success" and state.get("fingerprint") == _answer_fingerprint(
        connection
    )


def _saved_answer_connection() -> Any:
    store = _services()["store"]
    resolver = getattr(store, "resolve_answer_connection", None)
    if not callable(resolver):
        return None
    connection = resolver()
    if not isinstance(connection, ResolvedAnswerConnection):
        return connection
    service = _answer_service()
    docker_value = getattr(service, "running_in_docker", False)
    running_in_docker = (
        docker_value
        if isinstance(docker_value, bool)
        else str(docker_value).strip().lower()
        in {"1", "true", "yes", "y", "on"}
    )
    return replace(
        connection,
        # Keep ``base_url`` untouched for UI/public settings and derive a
        # task-only request target at the outbound boundary.
        outbound_base_url=outbound_url(connection.base_url, running_in_docker),
    )


def _answer_service() -> Any:
    services = _services()
    service = services.get("answer_connection_service")
    if service is None:
        service = services.get("answer_connection") or services.get("answer_service")
    return service


def _answer_ready(preferences: AccountPreferences) -> tuple[bool, Any]:
    if not preferences.answer_enabled:
        return True, None
    try:
        connection = _saved_answer_connection()
    except Exception:
        return False, None
    if connection is None:
        return False, None
    if not bool(getattr(connection, "enabled", False)):
        return False, None
    if not bool(getattr(connection, "has_api_key", False)):
        return False, None
    if not getattr(connection, "api_key", None):
        return False, None
    expected_fingerprint = _answer_fingerprint(connection)
    # A store implementation may persist its own safe test metadata.  When
    # present, require it as well as the in-memory marker so a direct store
    # mutation (outside the HTTP route) invalidates an old process-local mark.
    getter = getattr(_services()["store"], "get_answer_test_status", None)
    if callable(getter):
        try:
            status = getter()
        except Exception:
            return False, None
        if not isinstance(status, Mapping):
            return False, None
        if status.get("status") != "success" or status.get("fingerprint") != expected_fingerprint:
            return False, None
    elif not _answer_test_matches(connection):
        return False, None
    return True, connection


@tasks.get("/tasks")
def list_tasks():
    return jsonify(status=True, data=[_snapshot_data(item) for item in _manager().list_tasks()])


@tasks.post("/accounts/<account_id>/tasks")
def start_task(account_id: str):
    manager = _manager()
    store = _services()["store"]
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid task payload", "invalid_task", 400)
    if set(payload) - {"course_ids"}:
        return _error("Invalid task payload", "invalid_task", 400)

    # Account/profile reads, preference persistence, credential resolution,
    # and manager admission form one transaction-like boundary.  Account
    # mutations use this same lock, so neither side can observe a half-applied
    # identity/credential/enabled change.
    with _coordination_boundary(manager):
        profile = store.get_account(str(account_id))
        if profile is None:
            return _error("Account not found", "account_not_found", 404)
        if not _profile_enabled(profile):
            return _error("Account is disabled", "account_disabled", 409)

        preferences = store.get_preferences(str(account_id))
        if preferences is None:
            return _error("Account not found", "account_not_found", 404)
        if not _valid_preferences(preferences):
            return _error("Invalid preferences", "invalid_preferences", 400)
        try:
            selected_courses, replace_selection = _course_ids(payload, preferences)
        except ValueError as exc:
            code = str(exc)
            if code not in {"courses_required", "invalid_courses"}:
                code = "invalid_courses"
            message = "Courses are required" if code == "courses_required" else "Invalid courses"
            return _error(message, code, 400)

        if replace_selection:
            # Persist only the selection from this request.  All other
            # per-account preferences remain untouched.
            try:
                preferences = store.save_preferences(
                    str(account_id),
                    replace(preferences, selected_course_ids=selected_courses),
                )
            except (KeyError, TypeError, ValueError):
                return _error("Invalid preferences", "invalid_preferences", 400)

        # If this account already has a successful catalog fetch, reject an
        # explicitly stale ID at admission.  A cache miss is intentionally
        # deferred to ChaoxingStudyRunner, which validates the live catalog
        # in the account-owned session without adding another network call to
        # this short HTTP boundary.
        validator = getattr(_services().get("account_service"), "validate_course_ids", None)
        if callable(validator):
            try:
                valid_selection = validator(str(account_id), selected_courses)
            except Exception:
                valid_selection = None
            if valid_selection is False:
                return _error(
                    "Selected course IDs are invalid",
                    "course_selection_invalid",
                    400,
                )

        auth = store.get_account_auth(str(account_id))
        if isinstance(auth, Mapping):
            try:
                auth = AccountAuth(**dict(auth))
            except (TypeError, ValueError):
                auth = None
        if auth is None or not isinstance(auth, AccountAuth):
            return _error("Account credentials are not configured", "account_not_ready", 409)
        if not auth.password and not auth.cookies:
            return _error("Account credentials are not configured", "account_not_ready", 409)

        ready, answer = _answer_ready(preferences)
        if not ready:
            return _error("Answer connection has not been tested", "answer_not_ready", 409)

        answer_service = _answer_service()
        get_semaphore = getattr(answer_service, "get_semaphore", None)
        answer_semaphore = None
        if callable(get_semaphore):
            # The application factory wires this at construction time.
            # Keeping the assignment here also makes explicitly injected
            # managers obey the same shared answer gate in tests and embedding
            # integrations.
            try:
                answer_semaphore = get_semaphore()
                manager.answer_semaphore = answer_semaphore
            except Exception:
                answer_semaphore = None
        try:
            start_values = {
                "account_id": str(account_id),
                "course_ids": selected_courses,
                "preferences": preferences,
                "auth": auth,
                "answer": answer,
                "wait_started": False,
            }
            if callable(get_semaphore):
                start_values["answer_semaphore"] = answer_semaphore
            snapshot = manager.start(**start_values)
        except AccountTaskConflict:
            return _error("Account has an active task", "account_active", 409)
        except TaskCapacityReached:
            return _error("Task limit reached", "task_limit_reached", 409)
        except (TypeError, ValueError):
            return _error("Invalid task configuration", "invalid_task", 400)

    # Do not wait while holding the boundary: the worker's startup wrapper
    # acquires the manager lock before signalling its event.  A task that
    # completed and was evicted by an immediate subsequent run needs no wait.
    waiter = getattr(manager, "wait_until_started", None)
    if callable(waiter):
        try:
            waiter(snapshot.id)
        except TaskNotFound:
            pass
    return jsonify(status=True, data=_snapshot_data(snapshot)), 201


@tasks.get("/tasks/<task_id>")
def get_task(task_id: str):
    try:
        snapshot = _manager().get_snapshot(str(task_id))
    except TaskNotFound:
        return _error("Task not found", "task_not_found", 404)
    return jsonify(status=True, data=_snapshot_data(snapshot))


@tasks.get("/tasks/<task_id>/details")
def get_task_details(task_id: str):
    try:
        details = _manager().get_details(str(task_id))
    except TaskNotFound:
        return _error("Task not found", "task_not_found", 404)
    return jsonify(status=True, data=_details_data(details))


def _after_cursor() -> int:
    raw = request.args.get("after", "0")
    # int(" 1 ") is intentionally accepted as a harmless URL decoding
    # detail, while decimals, booleans, and blank values are rejected.
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError
    if raw.strip().startswith(("+", "-")):
        sign, digits = raw.strip()[0], raw.strip()[1:]
        if sign == "-" or not digits.isdigit():
            raise ValueError
    elif not raw.strip().isdigit():
        raise ValueError
    value = int(raw.strip())
    if value < 0:
        raise ValueError
    return value


@tasks.get("/tasks/<task_id>/logs")
def get_task_logs(task_id: str):
    try:
        after = _after_cursor()
    except (TypeError, ValueError, OverflowError):
        return _error("Invalid log cursor", "invalid_cursor", 400)
    try:
        page = _manager().get_logs(str(task_id), after=after)
    except TaskNotFound:
        return _error("Task not found", "task_not_found", 404)
    return jsonify(
        status=True,
        data={
            "items": [_log_data(item) for item in page.items],
            "next_cursor": page.next_cursor,
        },
    )


@tasks.post("/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    manager = _manager()
    try:
        snapshot = manager.cancel(str(task_id))
    except TaskNotFound:
        return _error("Task not found", "task_not_found", 404)
    except TaskNotRunning:
        # ``cancel`` owns the state check under the manager lock.  A terminal
        # transition can win between a caller's request and that check; keep
        # the race a stable conflict rather than leaking a 500.
        return _error("Task is not running", "task_not_running", 409)
    return jsonify(status=True, data=_snapshot_data(snapshot))


def record_answer_test(connection: Any, *, status: str) -> None:
    """Public helper for settings routes to record a safe connection probe.

    ``connection`` must be the currently saved, resolved connection.  The
    settings route compares the candidate draft before calling this helper so
    a request-only key cannot authorize a task.
    """

    _record_answer_test(connection, status=status)


def answer_fingerprint(connection: Any) -> str:
    return _answer_fingerprint(connection)


__all__ = [
    "answer_fingerprint",
    "invalidate_answer_test",
    "record_answer_test",
    "tasks",
]
