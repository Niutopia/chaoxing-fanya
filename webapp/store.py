"""Short-lived SQLite persistence for accounts and web settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .crypto import SecretBox
from .models import (
    AccountAuth,
    AccountPreferences,
    AccountProfile,
    AnswerConnection,
    ResolvedAnswerConnection,
    RuntimeSettings,
)


_SCHEMA_LOCK = threading.Lock()
_UNSET = object()
_VALID_VERIFICATION_STATUSES = {"unverified", "valid", "invalid"}
_ANSWER_SETTINGS_KEY = "answer_connection"
_RUNTIME_SETTINGS_KEY = "runtime"
_ANSWER_SECRET_KEY = "answer_api_key"
_ANSWER_TEST_STATUS = "_last_test_status"
_ANSWER_TEST_FINGERPRINT = "_last_test_fingerprint"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_token TEXT,
    cookies_token TEXT,
    auth_mode TEXT NOT NULL DEFAULT 'password',
    enabled INTEGER NOT NULL DEFAULT 1,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (enabled IN (0, 1)),
    CHECK (verification_status IN ('unverified', 'valid', 'invalid')),
    CHECK (auth_mode IN ('password', 'cookies'))
);

CREATE TABLE IF NOT EXISTS account_preferences (
    account_id TEXT PRIMARY KEY,
    selected_course_ids TEXT NOT NULL,
    speed REAL NOT NULL,
    jobs INTEGER NOT NULL,
    notopen_action TEXT NOT NULL,
    answer_enabled INTEGER NOT NULL,
    answer_cover_rate REAL NOT NULL,
    answer_auto_submit INTEGER NOT NULL,
    notification_config TEXT NOT NULL,
    ocr_config TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (answer_enabled IN (0, 1)),
    CHECK (answer_auto_submit IN (0, 1)),
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    value_token TEXT NOT NULL
);
"""


def _utc_now() -> str:
    """Return an ISO-8601 timestamp that includes its UTC offset."""

    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class SQLiteStore:
    """Encrypted account/settings store backed by SQLite.

    A connection is opened and closed for every public operation.  This keeps
    the store safe to use from Flask request threads and makes the SQLite
    transaction boundary explicit for each mutation.
    """

    def __init__(self, db_path: Path, secret_box: SecretBox):
        self.db_path = Path(db_path)
        self.secret_box = secret_box
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        # DDL is serialized so two app instances starting against the same
        # persistent volume cannot race while creating the schema.
        with _SCHEMA_LOCK:
            with self._connection() as connection:
                connection.executescript(_SCHEMA)
                # Older persistent volumes do not have an explicit
                # authentication mode.  Add it in place and normalize any
                # malformed legacy values before the app serves profiles.
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(accounts)")
                }
                added_auth_mode = "auth_mode" not in columns
                if added_auth_mode:
                    connection.execute(
                        "ALTER TABLE accounts ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'password'"
                    )
                # Normalize every persisted row, not only databases where the
                # mode column was newly added.  Older schemas (and a few
                # early development schemas) may contain an invalid mode or
                # both encrypted secrets.  Pick a deterministic mode, retain
                # exactly that mode's material, and invalidate verification
                # whenever the row is changed.
                rows = connection.execute(
                    "SELECT id, password_token, cookies_token, auth_mode, "
                    "verification_status, last_verified_at FROM accounts"
                ).fetchall()
                for row in rows:
                    raw_mode = row["auth_mode"]
                    if raw_mode in {"password", "cookies"}:
                        mode = raw_mode
                        # A freshly-added column has a default password value;
                        # infer the old cookie-only representation before
                        # applying the one-secret invariant.
                        if (
                            added_auth_mode
                            and mode == "password"
                            and row["password_token"] is None
                            and row["cookies_token"] is not None
                        ):
                            mode = "cookies"
                    elif row["password_token"] is None and row["cookies_token"] is not None:
                        mode = "cookies"
                    else:
                        mode = "password"

                    password_token = (
                        row["password_token"] if mode == "password" else None
                    )
                    cookies_token = (
                        row["cookies_token"] if mode == "cookies" else None
                    )
                    changed = (
                        raw_mode != mode
                        or row["password_token"] != password_token
                        or row["cookies_token"] != cookies_token
                    )
                    if not changed:
                        continue
                    connection.execute(
                        "UPDATE accounts SET auth_mode = ?, password_token = ?, "
                        "cookies_token = ?, verification_status = 'unverified', "
                        "last_verified_at = NULL, updated_at = ? WHERE id = ?",
                        (mode, password_token, cookies_token, _utc_now(), row["id"]),
                    )

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> AccountProfile:
        return AccountProfile(
            id=str(row["id"]),
            name=str(row["name"]),
            username=str(row["username"]),
            enabled=bool(row["enabled"]),
            has_secret=row["password_token"] is not None,
            has_cookies=row["cookies_token"] is not None,
            verification_status=row["verification_status"],
            last_verified_at=row["last_verified_at"],
            auth_mode=(row["auth_mode"] or "password"),
        )

    @staticmethod
    def _validate_verification_status(
        value: Literal["unverified", "valid", "invalid"] | str,
    ) -> str:
        if value not in _VALID_VERIFICATION_STATUSES:
            raise ValueError("verification_status must be unverified, valid, or invalid")
        return value

    @staticmethod
    def _validate_auth_mode(value: Literal["password", "cookies"] | str) -> str:
        if value not in {"password", "cookies"}:
            raise ValueError("auth_mode must be password or cookies")
        return value

    @staticmethod
    def _normalize_cookies(cookies: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(cookies, Mapping):
            raise TypeError("cookies must be a mapping")
        return {str(key): str(value) for key, value in cookies.items()}

    def _encrypt_cookies(self, cookies: Mapping[str, Any] | None) -> str | None:
        if cookies is None:
            return None
        normalized = self._normalize_cookies(cookies)
        if not normalized:
            return None
        return self.secret_box.encrypt(_json_dump(normalized))

    def _decrypt_cookies(self, token: str | None) -> dict[str, str]:
        if token is None:
            return {}
        value = _json_load(self.secret_box.decrypt(token), {})
        if not isinstance(value, Mapping):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    @staticmethod
    def _default_preferences() -> AccountPreferences:
        return AccountPreferences()

    @staticmethod
    def _preferences_from_row(row: sqlite3.Row) -> AccountPreferences:
        selected_course_ids = _json_load(row["selected_course_ids"], [])
        if not isinstance(selected_course_ids, list):
            selected_course_ids = []
        notification_config = _json_load(row["notification_config"], {})
        if not isinstance(notification_config, Mapping):
            notification_config = {}
        ocr_config = _json_load(row["ocr_config"], {})
        if not isinstance(ocr_config, Mapping):
            ocr_config = {}
        return AccountPreferences(
            selected_course_ids=[str(course_id) for course_id in selected_course_ids],
            speed=float(row["speed"]),
            jobs=int(row["jobs"]),
            notopen_action=row["notopen_action"],
            answer_enabled=bool(row["answer_enabled"]),
            answer_cover_rate=float(row["answer_cover_rate"]),
            answer_auto_submit=bool(row["answer_auto_submit"]),
            notification_config=dict(notification_config),
            ocr_config=dict(ocr_config),
        )

    @staticmethod
    def _answer_connection_from_config(
        config: Mapping[str, Any],
        *,
        has_api_key: bool,
        api_key: str | None = None,
        resolved: bool = False,
    ) -> AnswerConnection:
        defaults = AnswerConnection()
        values: dict[str, Any] = {
            "enabled": bool(config.get("enabled", defaults.enabled)),
            "base_url": str(config.get("base_url", defaults.base_url)),
            "model": str(config.get("model", defaults.model)),
            "has_api_key": bool(has_api_key),
            "timeout_seconds": float(
                config.get("timeout_seconds", defaults.timeout_seconds)
            ),
            "max_retries": int(config.get("max_retries", defaults.max_retries)),
            "max_concurrency": int(
                config.get("max_concurrency", defaults.max_concurrency)
            ),
        }
        if resolved:
            values["api_key"] = api_key
        connection_type = ResolvedAnswerConnection if resolved else AnswerConnection
        return connection_type(
            **values,
        )

    @staticmethod
    def _setting_value(connection: sqlite3.Connection, key: str) -> Any:
        row = connection.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return _json_load(row["value_json"], {}) if row else {}

    @staticmethod
    def _secret_token(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value_token FROM secrets WHERE key = ?", (key,)
        ).fetchone()
        return row["value_token"] if row else None

    def list_accounts(self) -> list[AccountProfile]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM accounts ORDER BY created_at ASC, id ASC"
            ).fetchall()
            return [self._profile_from_row(row) for row in rows]

    def get_account(self, account_id: str) -> AccountProfile | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (str(account_id),)
            ).fetchone()
            return self._profile_from_row(row) if row else None

    def get_account_auth(self, account_id: str) -> AccountAuth | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT username, password_token, cookies_token, auth_mode FROM accounts WHERE id = ?",
                (str(account_id),),
            ).fetchone()
            if row is None:
                return None
            password = (
                self.secret_box.decrypt(row["password_token"])
                if row["password_token"] is not None
                else ""
            )
            return AccountAuth(
                username=str(row["username"]),
                password=password,
                cookies=self._decrypt_cookies(row["cookies_token"]),
                auth_mode=self._validate_auth_mode(row["auth_mode"] or "password"),
            )

    def create_account(
        self,
        name: str,
        username: str,
        password: str | None,
        *,
        enabled: bool = True,
        verification_status: Literal["unverified", "valid", "invalid"] = "unverified",
        last_verified_at: str | None = None,
        cookies: Mapping[str, Any] | None = None,
        auth_mode: Literal["password", "cookies"] | None = None,
    ) -> AccountProfile:
        status = self._validate_verification_status(verification_status)
        if auth_mode is None:
            # Legacy callers supplied cookies without a mode and expected the
            # existing cookie-login preference.  The compatibility path still
            # chooses cookies, but it now persists only that source.
            auth_mode = "cookies" if cookies is not None else "password"
        mode = self._validate_auth_mode(auth_mode)
        account_id = str(uuid.uuid4())
        now = _utc_now()
        password_token = (
            self.secret_box.encrypt(password)
            if password is not None and mode == "password"
            else None
        )
        cookies_token = (
            self._encrypt_cookies(cookies)
            if cookies is not None and mode == "cookies"
            else None
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO accounts
                    (id, name, username, password_token, cookies_token, auth_mode,
                     enabled, verification_status, last_verified_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    str(name),
                    str(username),
                    password_token,
                    cookies_token,
                    mode,
                    int(bool(enabled)),
                    status,
                    last_verified_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return self._profile_from_row(row)

    def update_account(
        self,
        account_id: str,
        *,
        name: str | None = None,
        username: str | None = None,
        password: str | None | object = _UNSET,
        enabled: bool | None = None,
        verification_status: Literal["unverified", "valid", "invalid"]
        | str
        | None = None,
        last_verified_at: str | None | object = _UNSET,
        cookies: Mapping[str, Any] | None | object = _UNSET,
        auth_mode: Literal["password", "cookies"] | str | None = None,
    ) -> AccountProfile | None:
        account_id = str(account_id)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if existing is None:
                return None

            updates: list[str] = []
            values: list[Any] = []
            credential_changed = False
            current_mode = self._validate_auth_mode(existing["auth_mode"] or "password")
            selected_mode = (
                self._validate_auth_mode(auth_mode)
                if auth_mode is not None
                else current_mode
            )

            if name is not None:
                updates.append("name = ?")
                values.append(str(name))
            if username is not None:
                updates.append("username = ?")
                values.append(str(username))
                if str(username) != str(existing["username"]):
                    credential_changed = True
            if enabled is not None:
                updates.append("enabled = ?")
                values.append(int(bool(enabled)))

            current_password = (
                self.secret_box.decrypt(existing["password_token"])
                if existing["password_token"] is not None
                else ""
            )
            current_cookies = self._decrypt_cookies(existing["cookies_token"])
            password_supplied = (
                password is not _UNSET
                and password is not None
                and not (isinstance(password, str) and not password.strip())
            )
            cookies_supplied = cookies is not _UNSET
            normalized_cookies = (
                self._normalize_cookies(cookies)
                if cookies_supplied and cookies is not None
                else {}
            )
            if password_supplied and str(password) != current_password:
                credential_changed = True
            if cookies_supplied and normalized_cookies != current_cookies:
                credential_changed = True
            if auth_mode is not None:
                if selected_mode != current_mode:
                    credential_changed = True
                # Explicitly reaffirming a mode is still a material cleanup
                # when a legacy row carries the incompatible second secret.
                # Clearing that token must invalidate any old verification.
                if (
                    selected_mode == "password"
                    and existing["cookies_token"] is not None
                ) or (
                    selected_mode == "cookies"
                    and existing["password_token"] is not None
                ):
                    credential_changed = True

            if auth_mode is not None:
                # Selecting a source and clearing its incompatible secret are
                # one SQLite transaction.  A mode switch is credential
                # material even when the replacement field is blank.
                updates.append("auth_mode = ?")
                values.append(selected_mode)
                if selected_mode == "password":
                    password_token = (
                        self.secret_box.encrypt(str(password))
                        if password_supplied
                        else existing["password_token"]
                    )
                    cookies_token = None
                else:
                    password_token = None
                    cookies_token = (
                        self._encrypt_cookies(normalized_cookies)
                        if cookies_supplied
                        else existing["cookies_token"]
                    )
                updates.extend(["password_token = ?", "cookies_token = ?"])
                values.extend([password_token, cookies_token])
            else:
                # Compatibility path for direct store integrations.  The
                # account editor sends auth_mode explicitly; cookie refresh
                # callbacks use save_cookies so they do not reset verification.
                if password_supplied:
                    updates.append("password_token = ?")
                    values.append(self.secret_box.encrypt(str(password)))
                if cookies_supplied:
                    updates.append("cookies_token = ?")
                    values.append(self._encrypt_cookies(normalized_cookies))

            if credential_changed:
                # Verification is tied to the exact credential material.  A
                # caller cannot combine a stale valid marker with a change.
                updates.extend(["verification_status = ?", "last_verified_at = ?"])
                values.extend(["unverified", None])
            else:
                if verification_status is not None:
                    updates.append("verification_status = ?")
                    values.append(self._validate_verification_status(verification_status))
                if last_verified_at is not _UNSET:
                    updates.append("last_verified_at = ?")
                    values.append(last_verified_at)
            if updates:
                updates.append("updated_at = ?")
                values.extend([_utc_now(), account_id])
                connection.execute(
                    f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", values
                )
                existing = connection.execute(
                    "SELECT * FROM accounts WHERE id = ?", (account_id,)
                ).fetchone()
            return self._profile_from_row(existing)

    def delete_account(self, account_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM accounts WHERE id = ?", (str(account_id),)
            )
            return cursor.rowcount > 0

    def set_account_enabled(self, account_id: str, enabled: bool) -> AccountProfile | None:
        return self.update_account(account_id, enabled=enabled)

    def save_cookies(
        self, account_id: str, cookies: Mapping[str, Any] | None
    ) -> AccountProfile | None:
        """Persist refreshed session cookies without invalidating verification.

        Authentication callbacks run as part of a successful login.  Treating
        those server-issued cookies as a user credential edit would mark the
        account unverified immediately before the caller records success.
        """

        account_id = str(account_id)
        token = self._encrypt_cookies(cookies)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if existing is None:
                return None
            # Session refreshes are only persisted for cookie-auth accounts.
            # Password accounts may use an in-memory session for the current
            # request, but storing its cookies would violate the one-source
            # invariant and silently create a dual-secret row.
            if self._validate_auth_mode(existing["auth_mode"] or "password") != "cookies":
                return self._profile_from_row(existing)
            if token != existing["cookies_token"]:
                connection.execute(
                    "UPDATE accounts SET cookies_token = ?, updated_at = ? WHERE id = ?",
                    (token, _utc_now(), account_id),
                )
                existing = connection.execute(
                    "SELECT * FROM accounts WHERE id = ?", (account_id,)
                ).fetchone()
            return self._profile_from_row(existing)

    def record_verification(self, account_id: str, *, valid: bool) -> AccountProfile | None:
        """Persist a verification result without resolving credentials again."""

        return self.update_account(
            account_id,
            verification_status="valid" if valid else "invalid",
            last_verified_at=_utc_now(),
        )

    def get_preferences(self, account_id: str) -> AccountPreferences | None:
        with self._connection() as connection:
            account = connection.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (str(account_id),)
            ).fetchone()
            if account is None:
                return None
            row = connection.execute(
                "SELECT * FROM account_preferences WHERE account_id = ?",
                (str(account_id),),
            ).fetchone()
            return self._preferences_from_row(row) if row else self._default_preferences()

    def save_preferences(
        self,
        account_id: str,
        preferences: AccountPreferences | Mapping[str, Any],
    ) -> AccountPreferences:
        if isinstance(preferences, Mapping):
            preferences = AccountPreferences(**dict(preferences))
        if not isinstance(preferences, AccountPreferences):
            raise TypeError("preferences must be AccountPreferences")
        account_id = str(account_id)
        now = _utc_now()
        with self._connection() as connection:
            account = connection.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise KeyError(f"unknown account: {account_id}")
            connection.execute(
                """
                INSERT INTO account_preferences
                    (account_id, selected_course_ids, speed, jobs, notopen_action,
                     answer_enabled, answer_cover_rate, answer_auto_submit,
                     notification_config, ocr_config, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    selected_course_ids = excluded.selected_course_ids,
                    speed = excluded.speed,
                    jobs = excluded.jobs,
                    notopen_action = excluded.notopen_action,
                    answer_enabled = excluded.answer_enabled,
                    answer_cover_rate = excluded.answer_cover_rate,
                    answer_auto_submit = excluded.answer_auto_submit,
                    notification_config = excluded.notification_config,
                    ocr_config = excluded.ocr_config,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    _json_dump(list(preferences.selected_course_ids)),
                    float(preferences.speed),
                    int(preferences.jobs),
                    preferences.notopen_action,
                    int(bool(preferences.answer_enabled)),
                    float(preferences.answer_cover_rate),
                    int(bool(preferences.answer_auto_submit)),
                    _json_dump(dict(preferences.notification_config)),
                    _json_dump(dict(preferences.ocr_config)),
                    now,
                ),
            )
        return preferences

    def count_preferences(self, account_id: str) -> int:
        """Return the number of preference rows for an account (test helper)."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM account_preferences WHERE account_id = ?",
                (str(account_id),),
            ).fetchone()
            return int(row["count"])

    def get_answer_connection(self) -> AnswerConnection:
        with self._connection() as connection:
            config = self._setting_value(connection, _ANSWER_SETTINGS_KEY)
            if not isinstance(config, Mapping):
                config = {}
            return self._answer_connection_from_config(
                config,
                has_api_key=self._secret_token(connection, _ANSWER_SECRET_KEY)
                is not None,
            )

    def save_answer_connection(
        self,
        connection_settings: AnswerConnection
        | Mapping[str, Any]
        | None = None,
        *,
        enabled: bool | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        max_concurrency: int | None = None,
    ) -> AnswerConnection:
        """Persist answer settings while retaining an existing key by default."""

        if connection_settings is not None:
            if isinstance(connection_settings, AnswerConnection):
                source: Mapping[str, Any] = {
                    "enabled": connection_settings.enabled,
                    "base_url": connection_settings.base_url,
                    "model": connection_settings.model,
                    "timeout_seconds": connection_settings.timeout_seconds,
                    "max_retries": connection_settings.max_retries,
                    "max_concurrency": connection_settings.max_concurrency,
                }
                if api_key is None:
                    saved_api_key = getattr(connection_settings, "api_key", None)
                    if saved_api_key is not None:
                        api_key = saved_api_key
            elif isinstance(connection_settings, Mapping):
                source = connection_settings
                if api_key is None and source.get("api_key") is not None:
                    api_key = str(source["api_key"])
            else:
                raise TypeError("connection_settings must be AnswerConnection or mapping")
            if enabled is None and "enabled" in source:
                enabled = bool(source["enabled"])
            if base_url is None and "base_url" in source:
                base_url = str(source["base_url"])
            if model is None and "model" in source:
                model = str(source["model"])
            if timeout_seconds is None and "timeout_seconds" in source:
                timeout_seconds = float(source["timeout_seconds"])
            if max_retries is None and "max_retries" in source:
                max_retries = int(source["max_retries"])
            if max_concurrency is None and "max_concurrency" in source:
                max_concurrency = int(source["max_concurrency"])

        with self._connection() as db:
            current = self._setting_value(db, _ANSWER_SETTINGS_KEY)
            if not isinstance(current, Mapping):
                current = {}
            defaults = AnswerConnection()
            config = {
                "enabled": bool(
                    defaults.enabled if enabled is None else enabled
                ),
                "base_url": str(
                    defaults.base_url if base_url is None else base_url
                ),
                "model": str(defaults.model if model is None else model),
                "timeout_seconds": float(
                    current.get(
                        "timeout_seconds",
                        defaults.timeout_seconds,
                    )
                    if timeout_seconds is None
                    else timeout_seconds
                ),
                "max_retries": int(
                    current.get("max_retries", defaults.max_retries)
                    if max_retries is None
                    else max_retries
                ),
                "max_concurrency": int(
                    current.get("max_concurrency", defaults.max_concurrency)
                    if max_concurrency is None
                    else max_concurrency
                ),
            }
            # For a partial update, preserve every existing non-secret field.
            provided_fields = {
                "enabled": enabled,
                "base_url": base_url,
                "model": model,
            }
            for field_name, provided_value in provided_fields.items():
                if provided_value is None and field_name in current:
                    config[field_name] = current[field_name]
            # Any persisted connection mutation invalidates the previous
            # probe.  The private metadata is kept in ``settings`` rather
            # than the public AnswerConnection model and is never serialized
            # by the API.
            config.pop(_ANSWER_TEST_STATUS, None)
            config.pop(_ANSWER_TEST_FINGERPRINT, None)
            db.execute(
                """
                INSERT INTO settings (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (_ANSWER_SETTINGS_KEY, _json_dump(config)),
            )
            # An empty input is not a replacement.  Clearing is deliberately
            # a separate operation so a settings update cannot erase a key by
            # accident.
            if api_key:
                db.execute(
                    """
                    INSERT INTO secrets (key, value_token) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_token = excluded.value_token
                    """,
                    (_ANSWER_SECRET_KEY, self.secret_box.encrypt(api_key)),
                )
            return self._answer_connection_from_config(
                config,
                has_api_key=self._secret_token(db, _ANSWER_SECRET_KEY) is not None,
            )

    def clear_answer_key(self) -> AnswerConnection:
        with self._connection() as connection:
            connection.execute("DELETE FROM secrets WHERE key = ?", (_ANSWER_SECRET_KEY,))
            config = self._setting_value(connection, _ANSWER_SETTINGS_KEY)
            if not isinstance(config, Mapping):
                config = {}
            config = dict(config)
            config.pop(_ANSWER_TEST_STATUS, None)
            config.pop(_ANSWER_TEST_FINGERPRINT, None)
            connection.execute(
                """
                INSERT INTO settings (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (_ANSWER_SETTINGS_KEY, _json_dump(config)),
            )
            return self._answer_connection_from_config(config, has_api_key=False)

    def get_answer_test_status(self) -> dict[str, str | None]:
        """Return safe metadata for the last saved-connection probe.

        The fingerprint is a one-way value generated by the HTTP layer; no
        API key or other credential is stored in this metadata.
        """

        with self._connection() as connection:
            config = self._setting_value(connection, _ANSWER_SETTINGS_KEY)
            if not isinstance(config, Mapping):
                config = {}
            status = config.get(_ANSWER_TEST_STATUS)
            fingerprint = config.get(_ANSWER_TEST_FINGERPRINT)
            return {
                "status": str(status) if status is not None else None,
                "fingerprint": str(fingerprint) if fingerprint is not None else None,
            }

    def save_answer_test_status(self, status: str, fingerprint: str) -> None:
        """Persist safe last-probe status for the current connection identity."""

        if status not in {"success", "failed"}:
            raise ValueError("invalid answer test status")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("invalid answer test fingerprint")
        with self._connection() as connection:
            config = self._setting_value(connection, _ANSWER_SETTINGS_KEY)
            if not isinstance(config, Mapping):
                config = {}
            config = dict(config)
            config[_ANSWER_TEST_STATUS] = status
            config[_ANSWER_TEST_FINGERPRINT] = fingerprint
            connection.execute(
                """
                INSERT INTO settings (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (_ANSWER_SETTINGS_KEY, _json_dump(config)),
            )

    def resolve_answer_connection(self) -> AnswerConnection:
        """Return answer settings with the key decrypted for server-side use."""

        with self._connection() as connection:
            config = self._setting_value(connection, _ANSWER_SETTINGS_KEY)
            if not isinstance(config, Mapping):
                config = {}
            token = self._secret_token(connection, _ANSWER_SECRET_KEY)
            api_key = self.secret_box.decrypt(token) if token is not None else None
            return self._answer_connection_from_config(
                config,
                has_api_key=api_key is not None,
                api_key=api_key,
                resolved=True,
            )

    def get_runtime_settings(self) -> RuntimeSettings:
        with self._connection() as connection:
            value = self._setting_value(connection, _RUNTIME_SETTINGS_KEY)
            if not isinstance(value, Mapping):
                value = {}
            return RuntimeSettings(
                max_active_accounts=value.get(
                    "max_active_accounts", RuntimeSettings().max_active_accounts
                )
            )

    def save_runtime_settings(
        self,
        settings: RuntimeSettings | Mapping[str, Any] | None = None,
        *,
        max_active_accounts: int | None = None,
    ) -> RuntimeSettings:
        if settings is not None:
            if isinstance(settings, RuntimeSettings):
                if max_active_accounts is None:
                    max_active_accounts = settings.max_active_accounts
            elif isinstance(settings, Mapping):
                if max_active_accounts is None:
                    max_active_accounts = settings.get("max_active_accounts")
            else:
                raise TypeError("settings must be RuntimeSettings or mapping")
        with self._connection() as connection:
            if max_active_accounts is None:
                value = self._setting_value(connection, _RUNTIME_SETTINGS_KEY)
                if isinstance(value, Mapping):
                    max_active_accounts = value.get("max_active_accounts")
            if max_active_accounts is None:
                max_active_accounts = RuntimeSettings().max_active_accounts
            resolved = RuntimeSettings(max_active_accounts=max_active_accounts)
            connection.execute(
                """
                INSERT INTO settings (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (
                    _RUNTIME_SETTINGS_KEY,
                    _json_dump({"max_active_accounts": resolved.max_active_accounts}),
                ),
            )
        return resolved
