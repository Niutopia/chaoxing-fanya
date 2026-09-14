"""Focused RED/GREEN coverage for Final Unified Fix Round 3."""

from __future__ import annotations

import json
import sqlite3

import pytest

from webapp.crypto import SecretBox
from webapp.store import SQLiteStore


def _seed_existing_schema(
    tmp_path,
    *,
    auth_mode: str | None,
    password: str | None,
    cookies: dict[str, str] | None,
    verification_status: str = "valid",
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "chaoxing-web.sqlite3"
    secret_box = SecretBox(data_dir)
    password_token = secret_box.encrypt(password) if password is not None else None
    cookies_token = (
        secret_box.encrypt(json.dumps(cookies, ensure_ascii=False))
        if cookies is not None
        else None
    )
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            password_token TEXT,
            cookies_token TEXT,
            auth_mode TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            last_verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO accounts
            (id, name, username, password_token, cookies_token, auth_mode,
             enabled, verification_status, last_verified_at, created_at, updated_at)
        VALUES ('existing', '既有账号', 'existing-user', ?, ?, ?, 1, ?, ?, 'old', 'old')
        """,
        (
            password_token,
            cookies_token,
            auth_mode,
            verification_status,
            "2026-09-14T00:00:00+00:00" if verification_status == "valid" else None,
        ),
    )
    connection.commit()
    connection.close()
    return SQLiteStore(database_path, secret_box)


@pytest.mark.parametrize(
    ("auth_mode", "password", "cookies", "expected_mode", "expected_password", "expected_cookies"),
    [
        ("password", "password-secret", {"sid": "cookie-secret"}, "password", "password-secret", {}),
        ("cookies", "password-secret", {"sid": "cookie-secret"}, "cookies", "", {"sid": "cookie-secret"}),
        ("invalid-mode", "password-secret", {"sid": "cookie-secret"}, "password", "password-secret", {}),
        ("invalid-mode", None, {"sid": "cookie-secret"}, "cookies", "", {"sid": "cookie-secret"}),
    ],
)
def test_existing_schema_rows_normalize_mode_secrets_and_outputs(
    tmp_path,
    auth_mode,
    password,
    cookies,
    expected_mode,
    expected_password,
    expected_cookies,
):
    store = _seed_existing_schema(
        tmp_path,
        auth_mode=auth_mode,
        password=password,
        cookies=cookies,
    )

    listed = store.list_accounts()
    profile = store.get_account("existing")
    auth = store.get_account_auth("existing")

    assert listed[0] == profile
    assert profile.auth_mode == expected_mode
    assert profile.has_secret is bool(expected_password)
    assert profile.has_cookies is bool(expected_cookies)
    assert profile.verification_status == "unverified"
    assert profile.last_verified_at is None
    assert auth.auth_mode == expected_mode
    assert auth.password == expected_password
    assert auth.cookies == expected_cookies

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT password_token, cookies_token, auth_mode FROM accounts WHERE id = 'existing'"
        ).fetchone()
    assert bool(row[0]) is bool(expected_password)
    assert bool(row[1]) is bool(expected_cookies)
    assert row[2] == expected_mode


def test_existing_schema_single_allowed_secret_keeps_valid_verification(tmp_path):
    store = _seed_existing_schema(
        tmp_path,
        auth_mode="password",
        password="password-secret",
        cookies=None,
    )

    profile = store.get_account("existing")
    auth = store.get_account_auth("existing")

    assert profile.auth_mode == "password"
    assert profile.verification_status == "valid"
    assert profile.last_verified_at == "2026-09-14T00:00:00+00:00"
    assert auth.password == "password-secret"
    assert auth.cookies == {}


def test_compatibility_create_path_never_persists_dual_secret(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = SQLiteStore(data_dir / "chaoxing-web.sqlite3", SecretBox(data_dir))

    profile = store.create_account(
        "新账号",
        "new-user",
        "password-secret",
        cookies={"sid": "cookie-secret"},
    )
    auth = store.get_account_auth(profile.id)

    assert profile.auth_mode == "cookies"
    assert profile.has_secret is False
    assert profile.has_cookies is True
    assert auth.auth_mode == "cookies"
    assert auth.password == ""
    assert auth.cookies == {"sid": "cookie-secret"}

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT password_token, cookies_token FROM accounts WHERE id = ?",
            (profile.id,),
        ).fetchone()
    assert row[0] is None
    assert row[1] is not None


def test_cookie_refresh_does_not_add_incompatible_material_to_password_account(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = SQLiteStore(data_dir / "chaoxing-web.sqlite3", SecretBox(data_dir))
    profile = store.create_account(
        "密码账号",
        "password-user",
        "password-secret",
        auth_mode="password",
        verification_status="valid",
        last_verified_at="2026-09-14T00:00:00+00:00",
    )

    refreshed = store.save_cookies(profile.id, {"sid": "session-cookie"})
    auth = store.get_account_auth(profile.id)

    assert refreshed.has_cookies is False
    assert refreshed.verification_status == "valid"
    assert auth.cookies == {}
