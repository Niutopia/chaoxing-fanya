import sqlite3

import pytest

from webapp.crypto import SecretBox
from webapp.models import AccountPreferences, RuntimeSettings
from webapp.store import SQLiteStore


def test_account_secrets_are_encrypted_at_rest(tmp_path):
    box = SecretBox(tmp_path)
    store = SQLiteStore(tmp_path / "app.sqlite3", box)
    account = store.create_account("张三", "13800000000", "private-password")
    database_bytes = (tmp_path / "app.sqlite3").read_bytes()
    assert b"private-password" not in database_bytes
    assert store.get_account_auth(account.id).password == "private-password"


def test_preferences_are_scoped_by_account(tmp_path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    first = store.create_account("A", "100", "one")
    second = store.create_account("B", "200", "two")
    store.save_preferences(first.id, AccountPreferences(selected_course_ids=["math"]))
    store.save_preferences(second.id, AccountPreferences(selected_course_ids=["english"]))
    assert store.get_preferences(first.id).selected_course_ids == ["math"]
    assert store.get_preferences(second.id).selected_course_ids == ["english"]


def test_notification_and_ocr_secrets_are_encrypted_at_rest(tmp_path):
    database = tmp_path / "app.sqlite3"
    store = SQLiteStore(database, SecretBox(tmp_path))
    account = store.create_account("A", "100", "one")
    store.save_preferences(
        account.id,
        AccountPreferences(
            notification_config={
                "provider": "Telegram",
                "token": "notification-secret-token",
                "url": "https://notify.invalid/private-destination",
            },
            ocr_config={"provider": "openai", "api_key": "ocr-secret-key"},
        ),
    )

    database_bytes = database.read_bytes()
    assert b"notification-secret-token" not in database_bytes
    assert b"private-destination" not in database_bytes
    assert b"ocr-secret-key" not in database_bytes
    preferences = store.get_preferences(account.id)
    assert preferences.notification_config["token"] == "notification-secret-token"
    assert preferences.ocr_config["api_key"] == "ocr-secret-key"
    assert database.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_plaintext_preference_configs_are_migrated_and_compacted(tmp_path):
    database = tmp_path / "app.sqlite3"
    store = SQLiteStore(database, SecretBox(tmp_path))
    account = store.create_account("A", "100", "one")
    store.save_preferences(account.id, AccountPreferences())
    legacy_notification = '{"token":"legacy-notification-secret"}'
    legacy_ocr = '{"api_key":"legacy-ocr-secret"}'
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE account_preferences SET notification_config = ?, ocr_config = ? "
            "WHERE account_id = ?",
            (legacy_notification, legacy_ocr, account.id),
        )

    migrated = SQLiteStore(database, SecretBox(tmp_path))
    preferences = migrated.get_preferences(account.id)
    assert preferences.notification_config["token"] == "legacy-notification-secret"
    assert preferences.ocr_config["api_key"] == "legacy-ocr-secret"
    database_bytes = database.read_bytes()
    assert b"legacy-notification-secret" not in database_bytes
    assert b"legacy-ocr-secret" not in database_bytes


def test_secret_key_is_reused_with_owner_only_permissions(tmp_path):
    first = SecretBox(tmp_path)
    token = first.encrypt("value")
    second = SecretBox(tmp_path)
    assert second.decrypt(token) == "value"
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600


def test_deleting_account_cascades_preferences(tmp_path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    account = store.create_account("A", "100", "one")
    store.save_preferences(account.id, AccountPreferences(selected_course_ids=["math"]))
    store.delete_account(account.id)
    assert store.count_preferences(account.id) == 0


def test_answer_update_without_new_key_preserves_existing_key(tmp_path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    store.save_answer_connection(
        base_url="http://localhost:8849/v1",
        model="gemini-3.8-flash-high",
        api_key="secret-key",
    )
    store.save_answer_connection(
        base_url="http://localhost:8849/v1",
        model="gemini-3.8-flash-high",
        api_key=None,
    )
    assert store.resolve_answer_connection().api_key == "secret-key"


@pytest.mark.parametrize("limit", [0, 11])
def test_runtime_limit_rejects_values_outside_one_to_ten(limit):
    with pytest.raises(ValueError):
        RuntimeSettings(max_active_accounts=limit)


@pytest.mark.parametrize("limit", [1.9, True, "3"])
def test_runtime_store_rejects_non_integer_limits(tmp_path, limit):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    with pytest.raises(ValueError):
        store.save_runtime_settings(max_active_accounts=limit)
