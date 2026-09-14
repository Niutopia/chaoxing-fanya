"""Shared OpenAI-compatible answer connection support.

The web application stores the connection's API key in :class:`SQLiteStore`
and only resolves it for the short duration of a connection check or a task
startup.  This module deliberately keeps the key out of public dataclasses,
log messages, and exception text.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from .models import AnswerConnection


DEFAULT_BASE_URL = "http://localhost:8849/v1"
DEFAULT_MODEL = "gemini-3.8-flash-high"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_CONCURRENCY = 4


@dataclass(frozen=True)
class AnswerConnectionDraft:
    """A candidate answer connection used by the test endpoint.

    ``api_key`` is optional because the settings UI intentionally omits it
    when the saved key should be reused.  It is never serialized as part of a
    public settings response.
    """

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY


@dataclass(frozen=True)
class ConnectionTestResult:
    """Safe result returned from an answer connection check."""

    ok: bool
    model_found: bool
    code: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return only non-sensitive fields suitable for an API response."""

        return {"ok": bool(self.ok), "model_found": bool(self.model_found)}

    # ``to_dict`` is a small compatibility convenience for callers that use
    # the naming convention used by other service result objects.
    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        if key in {"ok", "model_found"}:
            return self.as_dict()[key]
        if key == "code":
            return self.code
        if key == "message":
            return self.message
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


def _invalid_url(message: str = "Answer API URL must use http or https") -> ValueError:
    return ValueError(message)


def _parse_base_url(base_url: str) -> SplitResult:
    """Parse and validate a user-provided HTTP(S) base URL.

    URL validation is intentionally strict.  In particular, credentials,
    query strings, and fragments would make it too easy to accidentally leak
    an API key through a URL or logs.  The returned split result is safe to
    rebuild without carrying those components forward.
    """

    if not isinstance(base_url, str):
        raise _invalid_url()
    value = base_url.strip()
    if not value or any(char.isspace() for char in value):
        raise _invalid_url()
    try:
        parsed = urlsplit(value)
        # Accessing these properties performs validation for malformed ports
        # and bracketed IPv6 hosts in urllib.parse.
        hostname = parsed.hostname
        _ = parsed.port
    except (AttributeError, ValueError):
        raise _invalid_url() from None

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise _invalid_url()
    # Reject even an empty trailing ``?``/``#``.  urlsplit represents those as
    # empty query/fragment values, but they are still not valid base settings.
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Answer API URL must not contain credentials, query, or fragment"
        )
    if "?" in value or "#" in value:
        raise ValueError(
            "Answer API URL must not contain credentials, query, or fragment"
        )
    return parsed


def normalize_completion_url(base_url: str) -> str:
    """Return the OpenAI chat-completions URL for ``base_url``.

    The setting is a base URL (normally ``.../v1``), but accepting an already
    normalized ``.../chat/completions`` value keeps the helper compatible with
    existing CLI configurations.
    """

    parsed = _parse_base_url(base_url)
    path = parsed.path.rstrip("/")
    suffix = "/chat/completions"
    if not path.endswith(suffix):
        path = f"{path}{suffix}"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def outbound_url(base_url: str, running_in_docker: bool) -> str:
    """Return the request URL, translating loopback only inside Docker.

    ``localhost`` and ``127.0.0.1`` refer to the container itself when the
    web app runs in Docker.  Other hosts are left untouched, and the stored
    user-visible URL is never rewritten.
    """

    parsed = _parse_base_url(base_url)
    netloc = parsed.netloc
    if running_in_docker and parsed.hostname and parsed.hostname.lower() in {
        "localhost",
        "127.0.0.1",
    }:
        host = "host.docker.internal"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        netloc = host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def models_url(base_url: str, running_in_docker: bool = False) -> str:
    """Build the OpenAI-compatible ``/models`` URL from a base URL."""

    parsed = _parse_base_url(base_url)
    path = parsed.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[: -len(suffix)].rstrip("/")
    path = f"{path}/models"
    candidate = urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))
    return outbound_url(candidate, running_in_docker)


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _safe_int(value: Any, *, minimum: int = 1) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _coerce_draft(draft: AnswerConnectionDraft | AnswerConnection | Mapping[str, Any]) -> AnswerConnectionDraft:
    if isinstance(draft, AnswerConnectionDraft):
        return draft
    if isinstance(draft, AnswerConnection):
        return AnswerConnectionDraft(
            enabled=draft.enabled,
            base_url=draft.base_url,
            model=draft.model,
            timeout_seconds=draft.timeout_seconds,
            max_retries=draft.max_retries,
            max_concurrency=draft.max_concurrency,
        )
    if isinstance(draft, Mapping):
        defaults = AnswerConnectionDraft()
        values = {
            "enabled": draft.get("enabled", defaults.enabled),
            "base_url": draft.get("base_url", defaults.base_url),
            "model": draft.get("model", defaults.model),
            "api_key": draft.get("api_key"),
            "timeout_seconds": draft.get(
                "timeout_seconds", defaults.timeout_seconds
            ),
            "max_retries": draft.get("max_retries", defaults.max_retries),
            "max_concurrency": draft.get(
                "max_concurrency", defaults.max_concurrency
            ),
        }
        return AnswerConnectionDraft(**values)
    raise TypeError("draft must be an AnswerConnectionDraft or mapping")


class AnswerConnectionService:
    """Test and coordinate the process-wide answer connection.

    The semaphore is intentionally owned by this service rather than by an
    individual AI provider.  A task runner can inject ``get_semaphore()``
    into every account's provider and thereby enforce one global limit.
    """

    def __init__(
        self,
        store,
        transport: httpx.BaseTransport | None = None,
        *,
        running_in_docker: bool = False,
        client: httpx.Client | None = None,
        http_client: httpx.Client | None = None,
        httpx_client: httpx.Client | None = None,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        self.store = store
        self.running_in_docker = (
            running_in_docker
            if isinstance(running_in_docker, bool)
            else str(running_in_docker).strip().lower()
            in {"1", "true", "yes", "y", "on"}
        )
        self._transport = transport
        self._client = (
            client
            if client is not None
            else http_client
            if http_client is not None
            else httpx_client
        )
        self._client_factory = client_factory
        self._client_lock = threading.RLock()
        self._semaphore_lock = threading.RLock()
        self._semaphore_size = self._stored_concurrency()
        self._semaphore = threading.BoundedSemaphore(self._semaphore_size)

    @property
    def client(self) -> httpx.Client | None:
        """Expose an optional injected client for tests/integrations."""

        return self._client

    @client.setter
    def client(self, value: httpx.Client | None) -> None:
        self._client = value

    def _stored_concurrency(self) -> int:
        try:
            settings = self.store.get_answer_connection()
            value = getattr(settings, "max_concurrency", DEFAULT_MAX_CONCURRENCY)
        except Exception:
            value = DEFAULT_MAX_CONCURRENCY
        result = _safe_int(value)
        return result if result is not None else DEFAULT_MAX_CONCURRENCY

    def get_semaphore(self) -> threading.BoundedSemaphore:
        """Return the one semaphore shared by all web AI providers."""

        return self._semaphore

    def refresh_semaphore(self) -> threading.BoundedSemaphore:
        """Apply a newly persisted concurrency setting for future tasks."""

        size = self._stored_concurrency()
        with self._semaphore_lock:
            if size != self._semaphore_size:
                # Existing providers retain the object they were given for
                # their current task.  New tasks receive the new process-wide
                # limit; no permit is leaked because replacement happens only
                # at a settings boundary.
                self._semaphore = threading.BoundedSemaphore(size)
                self._semaphore_size = size
            return self._semaphore

    def _client_for(self, timeout: float) -> tuple[httpx.Client, bool]:
        """Return a client and whether this service owns it."""

        if self._client is not None:
            return self._client, False
        if self._client_factory is not None:
            try:
                client = self._client_factory(timeout=timeout)
            except TypeError:
                client = self._client_factory()
            return client, True
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs), True

    @staticmethod
    def _result(
        ok: bool,
        model_found: bool,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=ok,
            model_found=model_found,
            code=code,
            message=message,
        )

    @staticmethod
    def _valid_draft(draft: AnswerConnectionDraft) -> str | None:
        if not isinstance(draft.enabled, bool):
            return "enabled must be a boolean"
        try:
            _parse_base_url(draft.base_url)
        except ValueError as exc:
            return str(exc)
        if not isinstance(draft.model, str) or not draft.model.strip():
            return "model must not be blank"
        if not _is_finite_number(draft.timeout_seconds) or float(draft.timeout_seconds) <= 0:
            return "timeout_seconds must be positive"
        if _safe_int(draft.max_retries, minimum=0) is None:
            return "max_retries must be a non-negative integer"
        if _safe_int(draft.max_concurrency) is None:
            return "max_concurrency must be a positive integer"
        if draft.api_key is not None and not isinstance(draft.api_key, str):
            return "api_key must be a string"
        return None

    def _resolve_api_key(self, draft: AnswerConnectionDraft) -> str | None:
        if isinstance(draft.api_key, str) and draft.api_key.strip():
            return draft.api_key
        try:
            resolved = self.store.resolve_answer_connection()
            key = getattr(resolved, "api_key", None)
            return key if isinstance(key, str) and key else None
        except Exception:
            return None

    @staticmethod
    def _model_found(body: Any, model: str) -> bool | None:
        if not isinstance(body, Mapping):
            return None
        entries = body.get("data")
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if isinstance(entry, Mapping) and str(entry.get("id", "")) == model:
                return True
        return False

    @staticmethod
    def _valid_completion_body(body: Any) -> bool:
        if not isinstance(body, Mapping):
            return False
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message")
            if isinstance(message, Mapping) and "content" in message:
                return True
            # A few compatible servers return a completion-style ``text``
            # field.  It is still a valid OpenAI response for reachability.
            if "text" in choice:
                return True
        return False

    def test(
        self,
        draft: AnswerConnectionDraft | AnswerConnection | Mapping[str, Any],
    ) -> ConnectionTestResult:
        """Check authentication and configured-model reachability.

        The models endpoint is preferred because it avoids spending a model
        token.  Compatible servers that do not expose it receive one minimal,
        non-streaming completion request instead.
        """

        try:
            candidate = _coerce_draft(draft)
        except (TypeError, ValueError):
            return self._result(
                False,
                False,
                code="answer_invalid",
                message="Invalid answer connection settings",
            )
        validation_error = self._valid_draft(candidate)
        if validation_error:
            return self._result(
                False,
                False,
                code="answer_invalid",
                message="Invalid answer connection settings",
            )

        api_key = self._resolve_api_key(candidate)
        if not api_key:
            return self._result(
                False,
                False,
                code="answer_auth_failed",
                message="Answer API authentication failed",
            )

        # Keep these values local.  Neither exception text nor result messages
        # include a URL with credentials or any request header.
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            model_endpoint = models_url(
                candidate.base_url, self.running_in_docker
            )
            completion_endpoint = outbound_url(
                normalize_completion_url(candidate.base_url),
                self.running_in_docker,
            )
            client, owned = self._client_for(float(candidate.timeout_seconds))
            try:
                response = client.get(
                    model_endpoint,
                    headers=headers,
                    timeout=float(candidate.timeout_seconds),
                )
            finally:
                if owned:
                    client.close()
        except (httpx.TimeoutException, TimeoutError):
            return self._result(
                False,
                False,
                code="answer_timeout",
                message="Answer API request timed out",
            )
        except httpx.HTTPError:
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API is unavailable",
            )
        except Exception:
            # Third-party clients may put headers, body data, or full URLs into
            # arbitrary exception messages.  Do not expose that text.
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API is unavailable",
            )

        if response.status_code in {401, 403}:
            return self._result(
                False,
                False,
                code="answer_auth_failed",
                message="Answer API authentication failed",
            )
        if response.status_code not in {404, 405}:
            if not 200 <= response.status_code < 300:
                return self._result(
                    False,
                    False,
                    code="answer_unavailable",
                    message="Answer API is unavailable",
                )
            try:
                model_found = self._model_found(response.json(), candidate.model)
            except Exception:
                model_found = None
            if model_found is None:
                return self._result(
                    False,
                    False,
                    code="answer_unavailable",
                    message="Answer API returned an invalid model list",
                )
            if not model_found:
                return self._result(
                    False,
                    False,
                    code="answer_model_missing",
                    message="Configured answer model was not found",
                )
            return self._result(True, True)

        # /models is optional for OpenAI-compatible servers.  Probe the
        # configured model with one token when it is unavailable.
        payload = {
            "model": candidate.model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "max_tokens": 1,
        }
        try:
            client, owned = self._client_for(float(candidate.timeout_seconds))
            try:
                response = client.post(
                    completion_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=float(candidate.timeout_seconds),
                )
            finally:
                if owned:
                    client.close()
        except (httpx.TimeoutException, TimeoutError):
            return self._result(
                False,
                False,
                code="answer_timeout",
                message="Answer API request timed out",
            )
        except httpx.HTTPError:
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API is unavailable",
            )
        except Exception:
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API is unavailable",
            )

        if response.status_code in {401, 403}:
            return self._result(
                False,
                False,
                code="answer_auth_failed",
                message="Answer API authentication failed",
            )
        if not 200 <= response.status_code < 300:
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API is unavailable",
            )
        try:
            valid_body = self._valid_completion_body(response.json())
        except Exception:
            valid_body = False
        if not valid_body:
            return self._result(
                False,
                False,
                code="answer_unavailable",
                message="Answer API returned an invalid completion",
            )
        return self._result(True, True)


__all__ = [
    "AnswerConnectionDraft",
    "AnswerConnectionService",
    "ConnectionTestResult",
    "models_url",
    "normalize_completion_url",
    "outbound_url",
]
