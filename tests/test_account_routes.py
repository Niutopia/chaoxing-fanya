"""Account-management HTTP API tests.

The route tests inject an in-memory fake account service so no test can make a
request to Chaoxing.  Persistence itself remains the real encrypted SQLite
store, which makes the isolation and secret-redaction assertions meaningful.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from webapp.account_service import (
    AccountService,
    AccountValidationError,
    CourseRetrievalError,
)
from webapp.crypto import SecretBox
from webapp.models import AccountPreferences
from webapp.store import SQLiteStore


class FakeTaskGuard:
    def __init__(self) -> None:
        self.active_account_ids: set[str] = set()

    def has_active_task(self, account_id: str) -> bool:
        return account_id in self.active_account_ids


class FakeAccountService:
    """Deterministic route collaborator with no external network access."""

    def __init__(self, store) -> None:
        self.store = store
        self._failures: dict[str, str] = {}
        self._fetch_counts: dict[str, int] = {}
        self._courses: dict[str, list[dict]] = {}

    def fail_verification(self, account_id: str, message: str) -> None:
        self._failures[account_id] = message

    def fetch_count(self, account_id: str) -> int:
        return self._fetch_counts.get(account_id, 0)

    def verify(self, account_id: str):
        if account_id in self._failures:
            # Import lazily so the test-first RED run fails on missing routes,
            # rather than failing during collection before production exists.
            from webapp.account_service import AccountValidationError

            raise AccountValidationError(self._failures[account_id])
        profile = self.store.get_account(account_id)
        if profile is None:
            raise KeyError(account_id)
        return replace(profile, verification_status="valid")

    def get_courses(self, account_id: str, refresh: bool = False) -> list[dict]:
        if self.store.get_account(account_id) is None:
            raise KeyError(account_id)
        if not refresh and account_id in self._courses:
            return list(self._courses[account_id])
        self._fetch_counts[account_id] = self._fetch_counts.get(account_id, 0) + 1
        courses = [{"id": f"course-{account_id}", "title": "测试课程"}]
        self._courses[account_id] = list(courses)
        return courses


class FakeSession:
    def __init__(self, cookies):
        self.cookies = dict(cookies)


class FakeChaoxingClient:
    """Deterministic Chaoxing double used by real AccountService tests."""

    def __init__(self, auth, cookie_update_callback, plan):
        self.auth = auth
        self.cookie_update_callback = cookie_update_callback
        self.session = FakeSession(auth.cookies)
        self.plan = dict(plan)
        self.login_calls: list[bool] = []

    def login(self, login_with_cookies=False):
        self.login_calls.append(login_with_cookies)
        if "login_exception" in self.plan:
            raise RuntimeError(self.plan["login_exception"])
        if "login_error" in self.plan:
            return {"status": False, "msg": self.plan["login_error"]}
        updated_cookies = self.plan.get("updated_cookies")
        if updated_cookies is not None:
            self.session.cookies.update(updated_cookies)
            self.cookie_update_callback(dict(self.session.cookies))
        return {"status": True, "msg": "ok"}

    def get_course_list(self):
        if "course_exception" in self.plan:
            raise RuntimeError(self.plan["course_exception"])
        return list(self.plan.get("courses", []))


class FakeChaoxingFactory:
    def __init__(self, plans):
        self.plans = list(plans)
        self.clients: list[FakeChaoxingClient] = []

    def __call__(self, *, auth, cookie_update_callback):
        plan = self.plans.pop(0) if self.plans else {}
        client = FakeChaoxingClient(auth, cookie_update_callback, plan)
        self.clients.append(client)
        return client


@pytest.fixture
def store(app):
    return app.extensions["services"]["store"]


@pytest.fixture
def saved_account(store):
    return store.create_account("已保存", "100", "original-password")


@pytest.fixture
def account_ids(store):
    first = store.create_account("第一账号", "100", "first-password")
    second = store.create_account("第二账号", "200", "second-password")
    return first.id, second.id


@pytest.fixture
def fake_account_service(app, store):
    service = FakeAccountService(store)
    app.extensions["services"]["account_service"] = service
    return service


@pytest.fixture
def task_guard(app):
    guard = FakeTaskGuard()
    app.extensions["services"]["task_guard"] = guard
    return guard


def default_preferences_payload() -> dict:
    return {
        "selected_course_ids": [],
        "speed": 1.0,
        "jobs": 4,
        "notopen_action": "retry",
        "answer_enabled": False,
        "answer_cover_rate": 0.9,
        "answer_auto_submit": False,
    }


def test_create_account_never_returns_password(client):
    response = client.post(
        "/api/accounts",
        json={
            "name": "张三",
            "username": "13800000000",
            "password": "secret-value",
        },
    )
    body = response.get_json()
    assert response.status_code == 201
    assert body["data"]["name"] == "张三"
    assert body["data"]["has_secret"] is True
    assert "password" not in body["data"]
    assert "secret-value" not in response.get_data(as_text=True)


def test_preferences_do_not_cross_accounts(client, account_ids):
    first, second = account_ids
    client.put(
        f"/api/accounts/{first}/preferences",
        json={
            "selected_course_ids": ["course-a"],
            "speed": 1.5,
            "jobs": 4,
            "notopen_action": "retry",
            "answer_enabled": True,
            "answer_cover_rate": 0.9,
            "answer_auto_submit": False,
        },
    )
    assert (
        client.get(f"/api/accounts/{first}/preferences").get_json()["data"][
            "selected_course_ids"
        ]
        == ["course-a"]
    )
    assert (
        client.get(f"/api/accounts/{second}/preferences").get_json()["data"][
            "selected_course_ids"
        ]
        == []
    )


def test_preferences_response_redacts_nested_notification_and_ocr_secrets(
    client, store, saved_account
):
    notification_url = "https://notify.example.invalid/provider-secret"
    notification_token = "notification-token-secret"
    notification_chat = "telegram-chat-secret"
    ocr_key = "ocr-api-key-secret"
    ocr_authorization = "Bearer ocr-authorization-secret"
    store.save_preferences(
        saved_account.id,
        AccountPreferences(
            notification_config={
                "provider": "telegram",
                "url": notification_url,
                "token": notification_token,
                "tg_chat_id": notification_chat,
                "nested": {"authorization": ocr_authorization},
            },
            ocr_config={
                "provider": "openai",
                "endpoint": "http://ocr.example.invalid/v1",
                "api_key": ocr_key,
                "nested": {"secret": "nested-ocr-secret"},
            },
        ),
    )

    response = client.get(f"/api/accounts/{saved_account.id}/preferences")
    body = response.get_json()
    body_text = response.get_data(as_text=True)

    assert response.status_code == 200
    for secret in (
        notification_url,
        notification_token,
        notification_chat,
        ocr_key,
        ocr_authorization,
        "nested-ocr-secret",
    ):
        assert secret not in body_text
    assert body["data"]["notification_config"]["has_token"] is True
    assert body["data"]["notification_config"]["has_url"] is True
    assert body["data"]["notification_config"]["has_tg_chat_id"] is True
    assert body["data"]["ocr_config"]["has_api_key"] is True
    assert body["data"]["ocr_config"]["endpoint"] == "http://ocr.example.invalid/v1"
    assert "fingerprint" not in body["data"]


def test_partial_preferences_preserve_omitted_and_blank_advanced_secrets(
    client, store, saved_account
):
    notification_url = "https://notify.example.invalid/provider-secret"
    notification_token = "notification-token-secret"
    notification_chat = "telegram-chat-secret"
    ocr_key = "ocr-api-key-secret"
    store.save_preferences(
        saved_account.id,
        AccountPreferences(
            selected_course_ids=["saved"],
            notification_config={
                "provider": "telegram",
                "url": notification_url,
                "token": notification_token,
                "tg_chat_id": notification_chat,
            },
            ocr_config={
                "provider": "openai",
                "endpoint": "http://ocr.example.invalid/v1",
                "api_key": ocr_key,
            },
        ),
    )

    launch_only = client.patch(
        f"/api/accounts/{saved_account.id}/preferences",
        json={"selected_course_ids": ["new-course"]},
    )
    assert launch_only.status_code == 200

    blank_secret_update = client.put(
        f"/api/accounts/{saved_account.id}/preferences",
        json={
            "notification_config": {"provider": "telegram", "url": "", "token": "", "tg_chat_id": ""},
            "ocr_config": {"endpoint": "http://ocr.example.invalid/v2", "api_key": ""},
        },
    )
    assert blank_secret_update.status_code == 200

    stored = store.get_preferences(saved_account.id)
    assert stored.selected_course_ids == ["new-course"]
    assert stored.notification_config["url"] == notification_url
    assert stored.notification_config["token"] == notification_token
    assert stored.notification_config["tg_chat_id"] == notification_chat
    assert stored.ocr_config["endpoint"] == "http://ocr.example.invalid/v2"
    assert stored.ocr_config["api_key"] == ocr_key


def test_patch_without_password_preserves_secret(client, store, saved_account):
    response = client.patch(
        f"/api/accounts/{saved_account.id}", json={"name": "新名称"}
    )
    assert response.status_code == 200
    assert store.get_account_auth(saved_account.id).password == "original-password"


def test_patch_with_blank_password_preserves_secret(client, store, saved_account):
    response = client.patch(
        f"/api/accounts/{saved_account.id}", json={"password": "   "}
    )
    assert response.status_code == 200
    assert store.get_account_auth(saved_account.id).password == "original-password"


def test_course_refresh_bypasses_per_account_cache(
    client, fake_account_service, saved_account
):
    client.get(f"/api/accounts/{saved_account.id}/courses")
    client.get(f"/api/accounts/{saved_account.id}/courses")
    client.get(f"/api/accounts/{saved_account.id}/courses?refresh=1")
    assert fake_account_service.fetch_count(saved_account.id) == 2


def test_delete_active_account_is_rejected(client, task_guard, saved_account):
    task_guard.active_account_ids.add(saved_account.id)
    response = client.delete(f"/api/accounts/{saved_account.id}")
    assert response.status_code == 409
    assert response.get_json()["code"] == "account_active"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "新名称"},
        {"username": "200"},
        {"password": "new-password"},
        {"cookies": "_uid=updated"},
    ],
)
def test_edit_active_account_is_rejected(client, task_guard, saved_account, payload):
    task_guard.active_account_ids.add(saved_account.id)
    response = client.patch(f"/api/accounts/{saved_account.id}", json=payload)
    assert response.status_code == 409
    assert response.get_json()["code"] == "account_active"


def test_active_account_blank_password_patch_is_a_noop(
    client, task_guard, store, saved_account
):
    task_guard.active_account_ids.add(saved_account.id)
    response = client.patch(
        f"/api/accounts/{saved_account.id}", json={"password": "\t  "}
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["name"] == saved_account.name
    assert store.get_account_auth(saved_account.id).password == "original-password"


def test_validation_exception_is_redacted_at_http_boundary(
    client, fake_account_service, store
):
    account = store.create_account(
        "已保存",
        "100",
        None,
        auth_mode="cookies",
        cookies={"sid": "known-cookie"},
    )
    fake_account_service.fail_verification(
        account.id,
        "cookie=_uid=known-cookie",
    )
    response = client.post(f"/api/accounts/{account.id}/verify")
    body_text = response.get_data(as_text=True)
    assert response.status_code == 401
    assert response.get_json()["code"] == "account_invalid"
    assert "known-cookie" not in body_text


def test_account_service_uses_fresh_scoped_clients_and_sessions(tmp_path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    first = store.create_account(
        "第一账号", "100", "first-password", cookies={"sid": "first-old"}
    )
    second = store.create_account(
        "第二账号", "200", "second-password", cookies={"sid": "second-old"}
    )
    factory = FakeChaoxingFactory(
        [
            {"updated_cookies": {"sid": "first-verified"}},
            {"updated_cookies": {"sid": "first-new"}, "courses": [{"id": "one"}]},
            {"updated_cookies": {"sid": "second-new"}, "courses": [{"id": "two"}]},
        ]
    )
    service = AccountService(store, factory)

    assert service.verify(first.id).verification_status == "valid"
    assert service.get_courses(first.id, refresh=True) == [{"id": "one"}]
    assert service.get_courses(second.id, refresh=True) == [{"id": "two"}]
    assert len(factory.clients) == 3
    assert factory.clients[0] is not factory.clients[1]
    assert factory.clients[0].session is not factory.clients[1].session
    assert factory.clients[1] is not factory.clients[2]
    assert factory.clients[1].session is not factory.clients[2].session
    assert all(client.login_calls == [True] for client in factory.clients)
    assert store.get_account_auth(first.id).cookies == {"sid": "first-new"}
    assert store.get_account_auth(second.id).cookies == {"sid": "second-new"}

    factory.clients[0].cookie_update_callback({"sid": "first-only"})
    assert store.get_account_auth(first.id).cookies == {"sid": "first-only"}
    assert store.get_account_auth(second.id).cookies == {"sid": "second-new"}


def test_account_service_redacts_login_failure_and_records_invalid(tmp_path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    account = store.create_account(
        "账号", "100", "known-password", auth_mode="password"
    )
    factory = FakeChaoxingFactory(
        [
            {
                "login_error": "password=known-password",
            }
        ]
    )
    service = AccountService(store, factory)

    with pytest.raises(AccountValidationError) as error:
        service.verify(account.id)
    assert "known-password" not in str(error.value)
    assert store.get_account(account.id).verification_status == "invalid"


def test_account_service_keeps_last_successful_courses_after_refresh_failure(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    account = store.create_account("账号", "100", "password")
    factory = FakeChaoxingFactory(
        [
            {"courses": [{"id": "stable"}]},
            {"course_exception": "temporary outage"},
        ]
    )
    service = AccountService(store, factory)

    assert service.get_courses(account.id) == [{"id": "stable"}]
    with pytest.raises(CourseRetrievalError):
        service.get_courses(account.id, refresh=True)
    assert service.get_courses(account.id) == [{"id": "stable"}]
    assert len(factory.clients) == 2


@pytest.mark.parametrize("cookies", ["malformed", "=missing-name", "name=ok;broken"])
def test_invalid_cookie_text_returns_400(client, cookies):
    response = client.post(
        "/api/accounts",
        json={"name": "张三", "username": "13800000000", "cookies": cookies},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_account"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speed", 0.9),
        ("speed", 2.1),
        ("jobs", 0),
        ("jobs", 11),
        ("answer_cover_rate", -0.1),
        ("answer_cover_rate", 1.1),
    ],
)
def test_invalid_preference_ranges_return_400(client, saved_account, field, value):
    payload = default_preferences_payload()
    payload[field] = value
    response = client.put(
        f"/api/accounts/{saved_account.id}/preferences", json=payload
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_preferences"


def test_list_enable_verify_and_delete(client, saved_account, fake_account_service):
    assert saved_account.id in {
        item["id"] for item in client.get("/api/accounts").get_json()["data"]
    }
    disabled = client.patch(
        f"/api/accounts/{saved_account.id}", json={"enabled": False}
    )
    assert disabled.get_json()["data"]["enabled"] is False
    fake_account_service.fail_verification(saved_account.id, "bad login")
    invalid = client.post(f"/api/accounts/{saved_account.id}/verify")
    assert invalid.status_code == 401
    assert invalid.get_json()["code"] == "account_invalid"
    client.patch(f"/api/accounts/{saved_account.id}", json={"enabled": True})
    assert client.delete(f"/api/accounts/{saved_account.id}").status_code == 204


@pytest.mark.parametrize("suffix", ["", "/verify", "/courses", "/preferences"])
def test_unknown_account_routes_are_stable(client, suffix):
    method = (
        client.put
        if suffix == "/preferences"
        else client.post
        if suffix == "/verify"
        else client.get
    )
    response = method(
        f"/api/accounts/missing{suffix}",
        json=default_preferences_payload() if suffix == "/preferences" else None,
    )
    assert response.status_code == 404
    assert response.get_json()["code"] == "account_not_found"
