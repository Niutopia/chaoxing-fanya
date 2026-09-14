"""Account-facing integrations with the Chaoxing client.

The web application owns one :class:`AccountService` instance.  It creates a
fresh Chaoxing client (and therefore a fresh HTTP session) for each account
operation, while keeping the encrypted store as the only source of account
credentials and cookies.  Public callers receive profiles and course data,
never the resolved ``AccountAuth`` value.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from .models import AccountAuth, AccountProfile


class AccountServiceError(Exception):
    """Base class for safe, user-facing account service errors."""


class AccountValidationError(AccountServiceError):
    """Raised when Chaoxing rejects an account's credentials or cookies."""


class CourseRetrievalError(AccountServiceError):
    """Raised when a fresh course-list request cannot be completed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_message(result: Any, fallback: str) -> str:
    """Extract a useful message without assuming a particular client type."""

    if isinstance(result, Mapping):
        message = result.get("msg")
        if message is None:
            message = result.get("message")
        if message is None:
            message = result.get("msg2")
        if message is not None and str(message).strip():
            return str(message)
    return fallback


class AccountService:
    """Validate accounts and retrieve their courses through isolated clients.

    ``chaoxing_factory`` is intentionally injected.  Production passes a
    factory that constructs ``api.base.Chaoxing``; route tests pass a fake and
    consequently never contact the Chaoxing service.
    """

    def __init__(self, store, chaoxing_factory: Callable[..., Any]):
        self.store = store
        self.chaoxing_factory = chaoxing_factory
        self._course_cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_lock = threading.RLock()

    def _auth(self, account_id: str) -> AccountAuth:
        auth = self.store.get_account_auth(account_id)
        if auth is None:
            raise KeyError(str(account_id))
        return auth

    @staticmethod
    def _safe_message(message: Any, auth: AccountAuth, fallback: str) -> str:
        """Keep backend messages useful while excluding secret values.

        Chaoxing normally returns a short fixed message, but a third-party
        client must not be able to accidentally put a password or cookie value
        into a response or exception.  Usernames are not secret and remain in
        diagnostics when a backend includes them.
        """

        text = str(message) if message is not None else ""
        for secret in (auth.password, *auth.cookies.values()):
            if secret:
                text = text.replace(str(secret), "[redacted]")
        return text.strip() or fallback

    def _client_with_auth(self, account_id: str) -> tuple[AccountAuth, Any]:
        """Construct an account-owned Chaoxing client and return its auth.

        The callback closes over the account ID, so a cookie update from one
        client can only update that account's encrypted cookie record.
        """

        auth = self._auth(account_id)
        client = self.chaoxing_factory(
            auth=auth,
            cookie_update_callback=lambda cookies: self.store.save_cookies(
                str(account_id), cookies
            ),
        )
        return auth, client

    def _client(self, account_id: str):
        """Construct an account-owned Chaoxing client.

        Keep this small compatibility helper for callers that only need the
        client.  Service operations that also need the resolved auth use
        ``_client_with_auth`` so the encrypted credential is resolved once.
        """

        return self._client_with_auth(account_id)[1]

    @staticmethod
    def _use_cookies(client: Any) -> bool:
        session = getattr(client, "session", None)
        cookies = getattr(session, "cookies", None)
        return bool(cookies)

    def _record_verification(self, account_id: str, *, valid: bool) -> None:
        """Record a verification result against the available store API.

        Newer stores expose ``record_verification``.  The Task 3 store on the
        compatibility branch exposes the same fields through
        ``update_account``; supporting both keeps this service usable with
        injected stores and avoids ever resolving credentials here.
        """

        recorder = getattr(self.store, "record_verification", None)
        if callable(recorder):
            recorder(str(account_id), valid=bool(valid))
            return
        updater = getattr(self.store, "update_account", None)
        if not callable(updater):
            return
        updater(
            str(account_id),
            verification_status="valid" if valid else "invalid",
            last_verified_at=_utc_now(),
        )

    def _login(self, account_id: str, client: Any, auth: AccountAuth):
        try:
            result = client.login(login_with_cookies=self._use_cookies(client))
        except AccountValidationError as exc:
            self._record_verification(account_id, valid=False)
            message = self._safe_message(
                str(exc), auth, "account validation failed"
            )
            raise AccountValidationError(message) from None
        except AccountServiceError:
            raise
        except Exception:
            # Do not preserve arbitrary third-party exception text: it can
            # contain request payloads, cookies, or authorization headers.
            self._record_verification(account_id, valid=False)
            raise AccountValidationError("account validation failed") from None

        status = bool(result.get("status")) if isinstance(result, Mapping) else False
        if not status:
            self._record_verification(account_id, valid=False)
            message = self._safe_message(
                _result_message(result, "account validation failed"),
                auth,
                "account validation failed",
            )
            raise AccountValidationError(message)
        self._record_verification(account_id, valid=True)
        return result

    def verify(self, account_id: str) -> AccountProfile:
        """Validate one account and persist the result."""

        auth, client = self._client_with_auth(account_id)
        self._login(account_id, client, auth)
        profile = self.store.get_account(account_id)
        if profile is None:
            raise KeyError(str(account_id))
        return profile

    def get_courses(self, account_id: str, refresh: bool = False) -> list[dict]:
        """Return a cached course list or fetch a fresh one for one account.

        Cache entries are keyed by account UUID and copied both on write and
        read.  A caller cannot mutate another request's result (or the cached
        value), and a failed fresh request leaves the previous successful
        value untouched.
        """

        account_id = str(account_id)
        # Resolve the account before consulting the cache so a deleted account
        # can never continue to read stale course data.
        if self.store.get_account(account_id) is None:
            raise KeyError(account_id)
        with self._cache_lock:
            if not refresh and account_id in self._course_cache:
                return copy.deepcopy(self._course_cache[account_id])

        auth, client = self._client_with_auth(account_id)
        self._login(account_id, client, auth)
        try:
            courses = client.get_course_list()
        except AccountServiceError:
            raise
        except Exception:
            # The exception text from a requests/parser stack is not safe to
            # expose because it can include request details or cookies.
            raise CourseRetrievalError("course retrieval failed") from None
        if courses is None:
            courses = []
        if not isinstance(courses, list):
            try:
                courses = list(courses)
            except TypeError:
                raise CourseRetrievalError("course retrieval failed") from None
        # Validate/copy before replacing the cache.  A failed deepcopy leaves
        # the prior successful entry intact and is reported as a retrieval
        # failure rather than becoming a Flask 500 response.
        try:
            copied_courses = copy.deepcopy(courses)
        except Exception:
            raise CourseRetrievalError("course retrieval failed") from None
        with self._cache_lock:
            self._course_cache[account_id] = copied_courses
        try:
            return copy.deepcopy(copied_courses)
        except Exception:
            # The cache is already a valid successful result.  A caller-side
            # copy failure must not discard it or expose the original object.
            raise CourseRetrievalError("course retrieval failed") from None

    def invalidate_courses(self, account_id: str) -> None:
        """Forget cached courses after an account credential mutation."""

        with self._cache_lock:
            self._course_cache.pop(str(account_id), None)
