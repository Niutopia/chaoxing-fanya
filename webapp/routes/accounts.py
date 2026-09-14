"""HTTP endpoints for account profiles and account-scoped preferences."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..account_service import (
    AccountServiceError,
    AccountValidationError,
    CourseRetrievalError,
)
from ..models import AccountPreferences


accounts = Blueprint("accounts", __name__, url_prefix="/api/accounts")


class NoopTaskGuard:
    """Task guard used until the task manager is composed into the app."""

    def has_active_task(self, account_id: str) -> bool:
        return False


_ACCOUNT_FIELDS = frozenset({"name", "username", "password", "cookies", "enabled"})
_PREFERENCE_FIELDS = frozenset(
    {
        "selected_course_ids",
        "speed",
        "jobs",
        "notopen_action",
        "answer_enabled",
        "answer_cover_rate",
        "answer_auto_submit",
        "notification_config",
        "ocr_config",
    }
)


def _services() -> Mapping[str, Any]:
    return current_app.extensions["services"]


def _error(message: str, code: str, status_code: int):
    return jsonify(status=False, msg=message, code=code), status_code


def _safe_validation_message(account_id: str, error: Exception) -> str:
    """Redact account secrets before returning a validation error.

    The production AccountService already sanitizes backend messages, but the
    service is injected at application composition time and may be replaced by
    another implementation.  Treat its exception text as untrusted at this
    HTTP boundary too, while retaining useful non-secret diagnostics.
    """

    fallback = "Account validation failed"
    try:
        message = str(error).strip()
    except Exception:
        return fallback
    if not message:
        return fallback

    try:
        auth = _services()["store"].get_account_auth(str(account_id))
    except Exception:
        return fallback
    if auth is None:
        return fallback

    secrets: list[str] = []
    password = getattr(auth, "password", None)
    if password:
        secrets.append(str(password))
    cookies = getattr(auth, "cookies", {})
    if isinstance(cookies, Mapping):
        secrets.extend(str(value) for value in cookies.values() if value)
    for secret in secrets:
        message = message.replace(secret, "[redacted]")
    return message or fallback


def _account_data(profile) -> dict[str, Any]:
    """Serialize only the intentionally public account-profile fields."""

    return {
        "id": profile.id,
        "name": profile.name,
        "username": profile.username,
        "enabled": profile.enabled,
        "has_secret": profile.has_secret,
        "has_cookies": profile.has_cookies,
        "verification_status": profile.verification_status,
        "last_verified_at": profile.last_verified_at,
    }


def _preferences_data(preferences: AccountPreferences) -> dict[str, Any]:
    """Serialize preferences without relying on dataclass implementation details."""

    return {
        "selected_course_ids": list(preferences.selected_course_ids),
        "speed": preferences.speed,
        "jobs": preferences.jobs,
        "notopen_action": preferences.notopen_action,
        "answer_enabled": preferences.answer_enabled,
        "answer_cover_rate": preferences.answer_cover_rate,
        "answer_auto_submit": preferences.answer_auto_submit,
        "notification_config": dict(preferences.notification_config),
        "ocr_config": dict(preferences.ocr_config),
    }


def _json_mapping() -> Mapping[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, Mapping) else None


def _parse_cookies(value: Any) -> dict[str, str] | None:
    """Parse semicolon-delimited ``name=value`` cookie text.

    Cookie values may contain ``=`` (split only at the first one). Empty
    segments are harmless, which permits the usual trailing semicolon. A
    mapping is accepted for internal/API clients that already parsed the
    cookie header, but every key still must be non-empty.
    """

    if value is None:
        return None
    if isinstance(value, Mapping):
        parsed: dict[str, str] = {}
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                raise ValueError("invalid cookies")
            parsed[key_text] = str(item)
        return parsed
    if not isinstance(value, str):
        raise ValueError("invalid cookies")

    parsed = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("invalid cookies")
        key, item = part.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("invalid cookies")
        parsed[key] = item.strip()
    return parsed


def _account_payload(payload: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = set(payload) - _ACCOUNT_FIELDS
    if unknown:
        raise ValueError("invalid account fields")

    values: dict[str, Any] = {}
    if not partial or "name" in payload:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid account name")
        values["name"] = name.strip()
    if not partial or "username" in payload:
        username = payload.get("username")
        if not isinstance(username, str) or not username.strip():
            raise ValueError("invalid account username")
        values["username"] = username.strip()
    if "password" in payload:
        password = payload["password"]
        if password is not None and not isinstance(password, str):
            raise ValueError("invalid account password")
        # Empty secret fields mean "keep the existing value" in the edit UI.
        # Omitting the field is also important for the active-task guard: a
        # blank-password PATCH is not a credential mutation.
        if isinstance(password, str) and password.strip():
            values["password"] = password
    if "cookies" in payload:
        values["cookies"] = _parse_cookies(payload["cookies"])
    if "enabled" in payload:
        enabled = payload["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("invalid account enabled value")
        values["enabled"] = enabled
    return values


def _numeric(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _preferences_payload(
    payload: Mapping[str, Any], current: AccountPreferences | None = None
) -> AccountPreferences:
    unknown = set(payload) - _PREFERENCE_FIELDS
    if unknown:
        raise ValueError("invalid preference fields")

    baseline = _preferences_data(current or AccountPreferences())
    baseline.update(payload)

    selected = baseline["selected_course_ids"]
    if not isinstance(selected, list) or not all(
        isinstance(course_id, str) for course_id in selected
    ):
        raise ValueError("invalid selected courses")

    speed = baseline["speed"]
    if not _numeric(speed) or not 1.0 <= float(speed) <= 2.0:
        raise ValueError("invalid speed")

    jobs = baseline["jobs"]
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 10:
        raise ValueError("invalid jobs")

    notopen_action = baseline["notopen_action"]
    if not isinstance(notopen_action, str) or notopen_action not in {"retry", "continue"}:
        raise ValueError("invalid notopen action")

    for field in ("answer_enabled", "answer_auto_submit"):
        if not isinstance(baseline[field], bool):
            raise ValueError("invalid answer preference")

    cover_rate = baseline["answer_cover_rate"]
    if not _numeric(cover_rate) or not 0.0 <= float(cover_rate) <= 1.0:
        raise ValueError("invalid answer cover rate")

    notification = baseline["notification_config"]
    ocr = baseline["ocr_config"]
    if not isinstance(notification, Mapping) or not isinstance(ocr, Mapping):
        raise ValueError("invalid preference config")

    return AccountPreferences(
        selected_course_ids=list(selected),
        speed=float(speed),
        jobs=jobs,
        notopen_action=notopen_action,
        answer_enabled=baseline["answer_enabled"],
        answer_cover_rate=float(cover_rate),
        answer_auto_submit=baseline["answer_auto_submit"],
        notification_config=dict(notification),
        ocr_config=dict(ocr),
    )


def _profile(account_id: str):
    return _services()["store"].get_account(str(account_id))


def _active(account_id: str) -> bool:
    guard = _services().get("task_guard") or NoopTaskGuard()
    has_active = getattr(guard, "has_active_task", None)
    return bool(has_active(str(account_id))) if callable(has_active) else False


def _edit_conflicts_with_active_task(account_id: str, values: Mapping[str, Any]) -> bool:
    """Return whether a profile mutation would race an active task.

    Credentials and identity must remain stable for the lifetime of a task.
    Re-enabling an account is harmless (and can repair an accidental disabled
    flag), but disabling or changing any other profile field is rejected.
    Preferences are deliberately handled by their own endpoint and are not
    part of this profile-level guard.
    """

    if not values or not _active(account_id):
        return False
    return values.get("enabled") is False or any(
        field != "enabled" for field in values
    )


@accounts.get("")
def list_accounts():
    store = _services()["store"]
    return jsonify(status=True, data=[_account_data(item) for item in store.list_accounts()])


@accounts.post("")
def create_account():
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid account payload", "invalid_account", 400)
    try:
        values = _account_payload(payload, partial=False)
    except (TypeError, ValueError):
        return _error("Invalid account payload", "invalid_account", 400)

    password = values.pop("password", None)
    cookies = values.pop("cookies", None)
    try:
        profile = _services()["store"].create_account(
            password=password,
            cookies=cookies,
            **values,
        )
    except (TypeError, ValueError):
        return _error("Invalid account payload", "invalid_account", 400)
    return jsonify(status=True, data=_account_data(profile)), 201


@accounts.patch("/<account_id>")
def update_account(account_id: str):
    profile = _profile(account_id)
    if profile is None:
        return _error("Account not found", "account_not_found", 404)
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid account payload", "invalid_account", 400)
    try:
        values = _account_payload(payload, partial=True)
    except (TypeError, ValueError):
        return _error("Invalid account payload", "invalid_account", 400)

    if _edit_conflicts_with_active_task(account_id, values):
        return _error("Account has an active task", "account_active", 409)

    try:
        updated = _services()["store"].update_account(str(account_id), **values)
    except (TypeError, ValueError):
        return _error("Invalid account payload", "invalid_account", 400)
    if updated is None:
        return _error("Account not found", "account_not_found", 404)
    service = _services().get("account_service")
    if service is not None and any(key in values for key in ("username", "password", "cookies")):
        invalidate = getattr(service, "invalidate_courses", None)
        if callable(invalidate):
            invalidate(str(account_id))
    return jsonify(status=True, data=_account_data(updated))


@accounts.get("/<account_id>")
def get_account(account_id: str):
    profile = _profile(account_id)
    if profile is None:
        return _error("Account not found", "account_not_found", 404)
    return jsonify(status=True, data=_account_data(profile))


@accounts.delete("/<account_id>")
def delete_account(account_id: str):
    if _profile(account_id) is None:
        return _error("Account not found", "account_not_found", 404)
    if _active(account_id):
        return _error("Account has an active task", "account_active", 409)
    if not _services()["store"].delete_account(str(account_id)):
        return _error("Account not found", "account_not_found", 404)
    service = _services().get("account_service")
    invalidate = getattr(service, "invalidate_courses", None) if service else None
    if callable(invalidate):
        invalidate(str(account_id))
    return "", 204


@accounts.post("/<account_id>/verify")
def verify_account(account_id: str):
    if _profile(account_id) is None:
        return _error("Account not found", "account_not_found", 404)
    service = _services()["account_service"]
    try:
        profile = service.verify(str(account_id))
    except KeyError:
        return _error("Account not found", "account_not_found", 404)
    except AccountValidationError as exc:
        return _error(
            _safe_validation_message(account_id, exc), "account_invalid", 401
        )
    except AccountServiceError:
        return _error("Account validation failed", "account_invalid", 401)
    return jsonify(status=True, data=_account_data(profile))


def _refresh_arg() -> bool:
    value = request.args.get("refresh", "0").strip().lower()
    if value in {"0", "false"}:
        return False
    if value in {"1", "true"}:
        return True
    raise ValueError("invalid refresh")


@accounts.get("/<account_id>/courses")
def get_courses(account_id: str):
    if _profile(account_id) is None:
        return _error("Account not found", "account_not_found", 404)
    try:
        refresh = _refresh_arg()
    except ValueError:
        return _error("Invalid refresh value", "invalid_refresh", 400)
    service = _services()["account_service"]
    try:
        courses = service.get_courses(str(account_id), refresh=refresh)
    except KeyError:
        return _error("Account not found", "account_not_found", 404)
    except AccountValidationError as exc:
        return _error(
            _safe_validation_message(account_id, exc), "account_invalid", 401
        )
    except CourseRetrievalError:
        return _error("Course retrieval failed", "courses_unavailable", 502)
    except AccountServiceError:
        return _error("Course retrieval failed", "courses_unavailable", 502)
    return jsonify(status=True, data=courses)


@accounts.get("/<account_id>/preferences")
def get_preferences(account_id: str):
    preferences = _services()["store"].get_preferences(str(account_id))
    if preferences is None:
        return _error("Account not found", "account_not_found", 404)
    return jsonify(status=True, data=_preferences_data(preferences))


@accounts.put("/<account_id>/preferences")
def put_preferences(account_id: str):
    store = _services()["store"]
    current = store.get_preferences(str(account_id))
    if current is None:
        return _error("Account not found", "account_not_found", 404)
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid preferences", "invalid_preferences", 400)
    try:
        preferences = _preferences_payload(payload, current)
        saved = store.save_preferences(str(account_id), preferences)
    except (TypeError, ValueError, KeyError):
        return _error("Invalid preferences", "invalid_preferences", 400)
    return jsonify(status=True, data=_preferences_data(saved))


__all__ = ["accounts", "NoopTaskGuard"]
