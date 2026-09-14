"""Global answer-connection and runtime settings endpoints."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..answer_connection import (
    AnswerConnectionDraft,
    AnswerConnectionService,
    ConnectionTestResult,
    normalize_completion_url,
)
from ..models import AnswerConnection, RuntimeSettings


settings = Blueprint("settings", __name__, url_prefix="/api/settings")

_ANSWER_FIELDS = frozenset(
    {
        "enabled",
        "base_url",
        "model",
        "api_key",
        "timeout_seconds",
        "max_retries",
        "max_concurrency",
    }
)
_RUNTIME_FIELDS = frozenset({"max_active_accounts"})


def _services() -> Mapping[str, Any]:
    return current_app.extensions["services"]


def _answer_service() -> AnswerConnectionService:
    # Keep both names as aliases for injected collaborators from early web
    # callers while exposing one canonical service in the application factory.
    services = _services()
    service = services.get("answer_connection_service")
    alias = services.get("answer_connection")
    short_alias = services.get("answer_service")
    default = services.get("_answer_connection_service_default")
    if default is not None:
        if service is default and alias is not default:
            service = alias
        elif service is default and alias is default and short_alias is not default:
            service = short_alias
    elif service is None:
        service = alias
    if service is None:
        service = services.get("answer_service")
    if service is None:
        raise RuntimeError("answer connection service is not configured")
    return service


def _error(message: str, code: str, status_code: int):
    return jsonify(status=False, msg=message, code=code), status_code


def _json_mapping() -> Mapping[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, Mapping) else None


def _connection_data(connection: AnswerConnection) -> dict[str, Any]:
    """Serialize an answer connection without resolving its key."""

    data: dict[str, Any] = {
        "enabled": bool(connection.enabled),
        "base_url": connection.base_url,
        "model": connection.model,
        "has_api_key": bool(connection.has_api_key),
        "timeout_seconds": connection.timeout_seconds,
        "max_retries": connection.max_retries,
        "max_concurrency": connection.max_concurrency,
    }
    if connection.has_api_key:
        # The public model intentionally exposes only presence.  A stable,
        # short mask helps the settings UI distinguish a configured key while
        # never carrying the plaintext value in the response.
        try:
            resolved = _services()["store"].resolve_answer_connection()
            key = getattr(resolved, "api_key", None)
        except Exception:
            key = None
        if isinstance(key, str) and len(key) > 4:
            data["api_key_mask"] = "Configured (••••" + key[-4:] + ")"
        else:
            data["api_key_mask"] = "Configured (••••)"
    else:
        data["api_key_mask"] = None
    return data


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _answer_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a partial answer settings update.

    Secrets are deliberately represented as ``None`` when omitted/blank so
    ``SQLiteStore.save_answer_connection`` retains the existing encrypted key.
    Clearing has its own endpoint below.
    """

    if set(payload) - _ANSWER_FIELDS:
        raise ValueError("invalid answer connection fields")
    values: dict[str, Any] = {}
    if "enabled" in payload:
        enabled = payload["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("invalid answer connection")
        values["enabled"] = enabled
    if "base_url" in payload:
        base_url = payload["base_url"]
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("invalid answer connection")
        # Keep exactly the user-visible base URL in storage; this call only
        # validates the scheme/credentials/query/fragment policy.
        normalize_completion_url(base_url)
        values["base_url"] = base_url.strip()
    if "model" in payload:
        model = payload["model"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError("invalid answer connection")
        values["model"] = model.strip()
    if "api_key" in payload:
        api_key = payload["api_key"]
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("invalid answer connection")
        # An omitted or blank key means "preserve".  Clear Key is explicit.
        if isinstance(api_key, str) and api_key.strip():
            values["api_key"] = api_key
        else:
            values["api_key"] = None
    if "timeout_seconds" in payload:
        timeout = payload["timeout_seconds"]
        if not _finite_number(timeout) or float(timeout) <= 0:
            raise ValueError("invalid answer connection")
        values["timeout_seconds"] = float(timeout)
    if "max_retries" in payload:
        retries = payload["max_retries"]
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError("invalid answer connection")
        values["max_retries"] = retries
    if "max_concurrency" in payload:
        concurrency = payload["max_concurrency"]
        if (
            isinstance(concurrency, bool)
            or not isinstance(concurrency, int)
            or concurrency < 1
        ):
            raise ValueError("invalid answer connection")
        values["max_concurrency"] = concurrency
    return values


def _test_draft(payload: Mapping[str, Any]) -> AnswerConnectionDraft:
    values = _answer_payload(payload)
    current = _services()["store"].resolve_answer_connection()
    key = values.pop("api_key", None)
    if key is None:
        key = getattr(current, "api_key", None)
    return AnswerConnectionDraft(
        enabled=values.pop("enabled", current.enabled),
        base_url=values.pop("base_url", current.base_url),
        model=values.pop("model", current.model),
        api_key=key,
        timeout_seconds=values.pop("timeout_seconds", current.timeout_seconds),
        max_retries=values.pop("max_retries", current.max_retries),
        max_concurrency=values.pop("max_concurrency", current.max_concurrency),
    )


@settings.get("/answer-connection")
def get_answer_connection():
    connection = _services()["store"].get_answer_connection()
    return jsonify(status=True, data=_connection_data(connection))


@settings.put("/answer-connection")
def put_answer_connection():
    payload = _json_mapping()
    if payload is None:
        return _error(
            "Invalid answer connection settings",
            "invalid_answer_connection",
            400,
        )
    try:
        values = _answer_payload(payload)
        connection = _services()["store"].save_answer_connection(**values)
        service = _answer_service()
        refresh = getattr(service, "refresh_semaphore", None)
        if callable(refresh):
            refresh()
    except (TypeError, ValueError):
        return _error(
            "Invalid answer connection settings",
            "invalid_answer_connection",
            400,
        )
    return jsonify(status=True, data=_connection_data(connection))


@settings.delete("/answer-connection/key")
def delete_answer_key():
    connection = _services()["store"].clear_answer_key()
    return jsonify(status=True, data=_connection_data(connection))


def _result_status(result: ConnectionTestResult) -> int:
    return {
        "answer_invalid": 400,
        "answer_auth_failed": 401,
        "answer_timeout": 504,
        "answer_model_missing": 422,
        "answer_unavailable": 502,
    }.get(result.code or "", 502)


def _result_message(result: ConnectionTestResult) -> str:
    # Prefer fixed messages over arbitrary injected-client text.  HTTP
    # libraries commonly put response bodies (which may contain credentials)
    # in exception/result messages.
    return {
        "answer_invalid": "Invalid answer connection settings",
        "answer_auth_failed": "Answer API authentication failed",
        "answer_timeout": "Answer API request timed out",
        "answer_model_missing": "Configured answer model was not found",
        "answer_unavailable": "Answer API is unavailable",
    }.get(result.code or "", "Answer API is unavailable")


@settings.post("/answer-connection/test")
def test_answer_connection():
    payload = _json_mapping()
    if payload is None:
        return _error(
            "Invalid answer connection settings",
            "invalid_answer_connection",
            400,
        )
    try:
        draft = _test_draft(payload)
    except (TypeError, ValueError, KeyError):
        return _error(
            "Invalid answer connection settings",
            "invalid_answer_connection",
            400,
        )
    try:
        result = _answer_service().test(draft)
    except Exception:
        # Third-party HTTP clients can include request headers and response
        # bodies in exception text.  Keep that material out of the browser.
        return _error("Answer API is unavailable", "answer_unavailable", 502)
    if not isinstance(result, ConnectionTestResult):
        # Permit a small injected fake service to return a mapping while still
        # keeping the browser response envelope stable.
        if isinstance(result, Mapping):
            result = ConnectionTestResult(
                ok=bool(result.get("ok")),
                model_found=bool(result.get("model_found")),
                code=result.get("code"),
                message=result.get("message"),
            )
        else:
            return _error("Answer API is unavailable", "answer_unavailable", 502)
    if result.ok:
        return jsonify(status=True, data=result.as_dict())
    return _error(
        _result_message(result),
        result.code or "answer_unavailable",
        _result_status(result),
    )


@settings.get("/runtime")
def get_runtime_settings():
    runtime = _services()["store"].get_runtime_settings()
    return jsonify(
        status=True,
        data={"max_active_accounts": runtime.max_active_accounts},
    )


@settings.put("/runtime")
def put_runtime_settings():
    payload = _json_mapping()
    if payload is None or set(payload) - _RUNTIME_FIELDS:
        return _error("Invalid runtime settings", "invalid_runtime", 400)
    try:
        runtime = _services()["store"].save_runtime_settings(**dict(payload))
    except (TypeError, ValueError):
        return _error("Invalid runtime settings", "invalid_runtime", 400)
    return jsonify(
        status=True,
        data={"max_active_accounts": runtime.max_active_accounts},
    )


__all__ = ["settings"]
