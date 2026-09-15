"""HTTP endpoints for account profiles and account-scoped preferences."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..account_service import (
    AccountServiceError,
    AccountValidationError,
    CourseRetrievalError,
)
from ..models import AccountPreferences
from ..limits import (
    MAX_ACCOUNT_NAME_LENGTH,
    MAX_ACCOUNT_PASSWORD_LENGTH,
    MAX_ACCOUNT_USERNAME_LENGTH,
    MAX_COOKIE_HEADER_LENGTH,
    MAX_COURSE_ID_LENGTH,
    MAX_SELECTED_COURSE_IDS,
    validate_config_shape,
    validate_cookie_mapping,
)


accounts = Blueprint("accounts", __name__, url_prefix="/api/accounts")


class NoopTaskGuard:
    """Task guard used until the task manager is composed into the app."""

    def has_active_task(self, account_id: str) -> bool:
        return False


_ACCOUNT_FIELDS = frozenset(
    {"name", "username", "password", "cookies", "enabled", "auth_mode", "authMode"}
)
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


def _coordination_boundary():
    """Return the lock shared with task-start admission."""

    services = _services()
    manager = services.get("task_manager")
    lock = getattr(manager, "lock", None)
    if lock is None:
        lock = services.get("account_task_lock")
    if lock is None:
        lock = current_app.extensions.get("account_task_lock")
    return lock if callable(getattr(lock, "__enter__", None)) else nullcontext()


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
        "auth_mode": getattr(profile, "auth_mode", "password"),
    }


def _preferences_data(preferences: AccountPreferences) -> dict[str, Any]:
    """Serialize preferences without returning account-owned secrets.

    Account notification and OCR settings are consumed by workers, so their
    stored values still need to remain available on the server.  They must
    never be sent back through this public endpoint, though: return only
    ordinary configuration values plus presence/mask metadata for fields that
    can contain credentials or private destinations.
    """

    return {
        "selected_course_ids": list(preferences.selected_course_ids),
        "speed": preferences.speed,
        "jobs": preferences.jobs,
        "notopen_action": preferences.notopen_action,
        "answer_enabled": preferences.answer_enabled,
        "answer_cover_rate": preferences.answer_cover_rate,
        "answer_auto_submit": preferences.answer_auto_submit,
        "notification_config": _safe_config(
            preferences.notification_config, notification=True
        ),
        "ocr_config": _safe_config(preferences.ocr_config),
    }


_CONFIG_MASK = "Configured (••••)"


def _config_key(key: Any) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _config_compact_key(key: Any) -> str:
    return _config_key(key).replace("_", "")


def _is_config_metadata_key(key: Any) -> bool:
    normalized = _config_key(key)
    return (
        normalized.startswith("has_")
        or normalized.endswith("_mask")
        or normalized.endswith("_configured")
    )


def _is_sensitive_config_key(key: Any, *, notification: bool = False) -> bool:
    """Recognize credential and private-destination aliases in config maps."""

    compact = _config_compact_key(key)
    if not compact:
        return False

    # Keep this deliberately provider-agnostic.  Integrations frequently use
    # ``key``/``apikey``/``authorization`` aliases, including nested maps.
    if (
        compact == "key"
        or any(
            marker in compact
            for marker in (
                "apikey",
                "accesstoken",
                "accesskey",
                "token",
                "secret",
                "authorization",
                "password",
                "credential",
                "cookie",
                "privatekey",
            )
        )
    ):
        return True

    if notification and any(
        marker in compact
        for marker in ("url", "uri", "endpoint", "webhook", "chatid", "chat")
    ):
        return True
    return False


def _masked_config_value(value: Any) -> tuple[bool, str | None]:
    """Return presence/mask metadata without retaining the source value."""

    if value is None:
        return False, None
    if isinstance(value, Mapping):
        present = bool(value)
    elif isinstance(value, (list, tuple, set)):
        present = bool(value)
    else:
        present = bool(str(value).strip())
    return present, _CONFIG_MASK if present else None


def _safe_config(value: Any, *, notification: bool = False) -> dict[str, Any]:
    """Deeply redact secret-bearing config values for a public response."""

    if not isinstance(value, Mapping):
        return {}

    result: dict[str, Any] = {}
    for key, item in value.items():
        # A previous public response may be submitted by an API client.  Do
        # not persist its derived metadata as if it were provider config.
        if _is_config_metadata_key(key):
            continue
        if _is_sensitive_config_key(key, notification=notification):
            present, mask = _masked_config_value(item)
            normalized = _config_key(key)
            result[f"has_{normalized}"] = present
            result[f"{normalized}_mask"] = mask
            continue
        if isinstance(item, Mapping):
            result[str(key)] = _safe_config(item, notification=notification)
        elif isinstance(item, list):
            result[str(key)] = [
                _safe_config(entry, notification=notification)
                if isinstance(entry, Mapping)
                else entry
                for entry in item
            ]
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = item
    return result


def _merge_config(
    current: Any, incoming: Any, *, notification: bool = False
) -> dict[str, Any]:
    """Merge editable config while preserving blank secret replacements.

    The worker needs the raw server-side values, while the settings UI only
    sends a non-blank replacement when the user intentionally changes a
    secret.  Omitted and blank sensitive fields therefore leave the stored
    value untouched.  Non-secret fields retain normal partial-update
    semantics, including nested provider maps.
    """

    if not isinstance(incoming, Mapping):
        raise ValueError("invalid preference config")
    result: dict[str, Any] = (
        deepcopy(dict(current)) if isinstance(current, Mapping) else {}
    )
    for raw_key, item in incoming.items():
        key = str(raw_key)
        if _is_config_metadata_key(key):
            continue
        if isinstance(item, Mapping):
            result[key] = _merge_config(
                result.get(key), item, notification=notification
            )
            continue
        if _is_sensitive_config_key(key, notification=notification):
            if item is None or (
                isinstance(item, str) and not item.strip()
            ):
                continue
        result[key] = item
    return result


_OCR_ENDPOINT_KEYS = frozenset(
    {"endpoint", "ocrendpoint", "httpendpoint", "fallbackendpoint", "url"}
)


def _ocr_endpoint(config: Any) -> str | None:
    """Return the configured OCR endpoint from a provider mapping.

    OCR integrations in the wild use both ``endpoint`` and the legacy
    ``ocr_endpoint``/``http_endpoint`` spellings.  We compare only the
    endpoint value; loopback and private-network addresses remain valid and
    are deliberately not blocked here.
    """

    if not isinstance(config, Mapping):
        return None
    for raw_key, value in config.items():
        compact = _config_compact_key(raw_key)
        if compact in _OCR_ENDPOINT_KEYS and isinstance(value, str):
            candidate = value.strip()
            if candidate:
                # A trailing slash does not identify a different OCR service.
                return candidate.rstrip("/")
    return None


def _ocr_key_is_explicit(config: Any) -> bool:
    """Whether an incoming OCR config contains a non-blank replacement key."""

    if not isinstance(config, Mapping):
        return False
    for raw_key, value in config.items():
        if _is_sensitive_config_key(raw_key, notification=False):
            if value is not None and not isinstance(value, (Mapping, list)):
                if str(value).strip():
                    return True
        elif isinstance(value, Mapping):
            if _ocr_key_is_explicit(value):
                return True
        elif isinstance(value, list):
            if any(_ocr_key_is_explicit(item) for item in value):
                return True
    return False


def _validate_ocr_endpoint_change(
    current: AccountPreferences, incoming: Any
) -> None:
    """Require a fresh OCR key when a saved custom endpoint changes.

    Blank/omitted keys normally mean "keep the saved key" for partial
    preference updates.  That rule is unsafe across endpoints, because the
    old credential could be sent to an unrelated service.  Requiring a
    non-blank key for an endpoint change preserves local/private endpoints
    while preventing accidental cross-service credential reuse.
    """

    if not isinstance(incoming, Mapping):
        return
    old_endpoint = _ocr_endpoint(current.ocr_config)
    new_endpoint = _ocr_endpoint(incoming)
    if new_endpoint is None:
        return
    if (old_endpoint or "") == new_endpoint:
        return
    if not _ocr_key_is_explicit(incoming):
        raise ValueError("a new OCR endpoint requires an explicit API key")


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
        try:
            return validate_cookie_mapping(value)
        except (TypeError, ValueError):
            raise ValueError("invalid cookies") from None
    if not isinstance(value, str):
        raise ValueError("invalid cookies")
    if len(value) > MAX_COOKIE_HEADER_LENGTH:
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
    try:
        return validate_cookie_mapping(parsed)
    except (TypeError, ValueError):
        raise ValueError("invalid cookies") from None


def _account_payload(payload: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = set(payload) - _ACCOUNT_FIELDS
    if unknown:
        raise ValueError("invalid account fields")

    values: dict[str, Any] = {}
    if not partial or "name" in payload:
        name = payload.get("name")
        if (
            not isinstance(name, str)
            or len(name) > MAX_ACCOUNT_NAME_LENGTH
            or not name.strip()
        ):
            raise ValueError("invalid account name")
        values["name"] = name.strip()
    if not partial or "username" in payload:
        username = payload.get("username")
        if (
            not isinstance(username, str)
            or len(username) > MAX_ACCOUNT_USERNAME_LENGTH
            or not username.strip()
        ):
            raise ValueError("invalid account username")
        values["username"] = username.strip()
    if "password" in payload:
        password = payload["password"]
        if password is not None and (
            not isinstance(password, str)
            or len(password) > MAX_ACCOUNT_PASSWORD_LENGTH
        ):
            raise ValueError("invalid account password")
        # Empty secret fields mean "keep the existing value" in the edit UI.
        # Omitting the field is also important for the active-task guard: a
        # blank-password PATCH is not a credential mutation.
        if isinstance(password, str) and password.strip():
            values["password"] = password
    if "cookies" in payload:
        raw_cookies = payload["cookies"]
        # The edit form uses a blank Cookie Header as “preserve existing”.
        # A real mapping/header still follows the normal parser and can be
        # used to replace the stored encrypted value.
        if not (isinstance(raw_cookies, str) and not raw_cookies.strip()):
            values["cookies"] = _parse_cookies(raw_cookies)
    if "enabled" in payload:
        enabled = payload["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("invalid account enabled value")
        values["enabled"] = enabled
    raw_auth_mode = payload.get("auth_mode", payload.get("authMode"))
    if raw_auth_mode is not None:
        if raw_auth_mode not in {"password", "cookies"}:
            raise ValueError("invalid account auth mode")
        values["auth_mode"] = raw_auth_mode
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

    current = current or AccountPreferences()
    # Build the baseline from the server-side model rather than the public
    # serializer above.  The latter intentionally contains only masks, and
    # feeding it back into persistence would discard the real worker config.
    baseline: dict[str, Any] = {
        field: deepcopy(getattr(current, field))
        for field in (
            "selected_course_ids",
            "speed",
            "jobs",
            "notopen_action",
            "answer_enabled",
            "answer_cover_rate",
            "answer_auto_submit",
        )
    }
    baseline["notification_config"] = deepcopy(current.notification_config)
    baseline["ocr_config"] = deepcopy(current.ocr_config)
    for field, value in payload.items():
        if field == "notification_config":
            try:
                validate_config_shape(value, field_name="notification_config")
            except (TypeError, ValueError):
                raise ValueError("invalid preference config") from None
            baseline[field] = _merge_config(
                current.notification_config, value, notification=True
            )
        elif field == "ocr_config":
            try:
                validate_config_shape(value, field_name="ocr_config")
            except (TypeError, ValueError):
                raise ValueError("invalid preference config") from None
            _validate_ocr_endpoint_change(current, value)
            baseline[field] = _merge_config(current.ocr_config, value)
        else:
            baseline[field] = value

    selected = baseline["selected_course_ids"]
    if not isinstance(selected, list) or not all(
        isinstance(course_id, str) for course_id in selected
    ):
        raise ValueError("invalid selected courses")
    if len(selected) > MAX_SELECTED_COURSE_IDS or any(
        len(course_id) > MAX_COURSE_ID_LENGTH for course_id in selected
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
    try:
        validate_config_shape(notification, field_name="notification_config")
        validate_config_shape(ocr, field_name="ocr_config")
    except (TypeError, ValueError):
        raise ValueError("invalid preference config") from None

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
    services = _services()
    # ``create_app`` wires the process-local TaskManager as the guard.  Keep
    # the separately named extension as an integration seam for older route
    # tests/custom hosts, but fall back to the real manager whenever a host
    # did not provide a guard explicitly.
    guard = services.get("task_guard")
    manager = services.get("task_manager")
    if guard is None or isinstance(guard, NoopTaskGuard):
        guard = manager
    guard = guard or NoopTaskGuard()
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
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid account payload", "invalid_account", 400)
    try:
        values = _account_payload(payload, partial=True)
    except (TypeError, ValueError):
        return _error("Invalid account payload", "invalid_account", 400)

    with _coordination_boundary():
        profile = _profile(account_id)
        if profile is None:
            return _error("Account not found", "account_not_found", 404)

        if _edit_conflicts_with_active_task(account_id, values):
            return _error("Account has an active task", "account_active", 409)

        try:
            updated = _services()["store"].update_account(str(account_id), **values)
        except (TypeError, ValueError):
            return _error("Invalid account payload", "invalid_account", 400)
        if updated is None:
            return _error("Account not found", "account_not_found", 404)
        service = _services().get("account_service")
        if service is not None and any(
            key in values for key in ("username", "password", "cookies", "auth_mode")
        ):
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
    with _coordination_boundary():
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
@accounts.patch("/<account_id>/preferences")
def put_preferences(account_id: str):
    store = _services()["store"]
    payload = _json_mapping()
    if payload is None:
        return _error("Invalid preferences", "invalid_preferences", 400)
    with _coordination_boundary():
        current = store.get_preferences(str(account_id))
        if current is None:
            return _error("Account not found", "account_not_found", 404)
        try:
            preferences = _preferences_payload(payload, current)
            saved = store.save_preferences(str(account_id), preferences)
        except (TypeError, ValueError, KeyError):
            return _error("Invalid preferences", "invalid_preferences", 400)
        return jsonify(status=True, data=_preferences_data(saved))


__all__ = ["accounts", "NoopTaskGuard"]
