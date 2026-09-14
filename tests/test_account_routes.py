"""Account-management HTTP API tests.

The route tests inject an in-memory fake account service so no test can make a
request to Chaoxing.  Persistence itself remains the real encrypted SQLite
store, which makes the isolation and secret-redaction assertions meaningful.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

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


def test_patch_without_password_preserves_secret(client, store, saved_account):
    response = client.patch(
        f"/api/accounts/{saved_account.id}", json={"name": "新名称"}
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
