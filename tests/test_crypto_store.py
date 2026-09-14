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
