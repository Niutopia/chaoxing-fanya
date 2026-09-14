"""HTTP integration tests for process-local task routes."""

from __future__ import annotations

import threading

import pytest

from webapp import create_app
from webapp.models import AccountPreferences
from webapp.task_manager import TaskManager


class FakeRunner:
    """Runner that records contexts and waits for an explicit release."""

    def __init__(self) -> None:
        self.contexts = {}
        self._release: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def run(self, context):
        with self._lock:
            self.contexts[context.task_id] = context
            release = self._release.setdefault(context.task_id, threading.Event())
        release.wait(timeout=2)

    def log(self, task_id: str, message: str) -> None:
        self.contexts[task_id].reporter.append_log(message)

    def release(self, task_id: str) -> None:
        with self._lock:
            self._release.setdefault(task_id, threading.Event()).set()


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def app(tmp_path, fake_runner):
    manager = TaskManager(runner=fake_runner, max_active_accounts=3)
    application = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "TASK_MANAGER": manager,
        }
    )
    yield application
    for snapshot in manager.list_tasks():
        fake_runner.release(snapshot.id)
        manager.wait(snapshot.id, timeout=1)


@pytest.fixture
def store(app):
    return app.extensions["services"]["store"]


@pytest.fixture
def prepared_accounts(store):
    first = store.create_account("第一账号", "100", "first-password")
    second = store.create_account("第二账号", "200", "second-password")
    return first, second


@pytest.fixture
def prepared_account(prepared_accounts):
    return prepared_accounts[0]


@pytest.fixture
def running_task(client, prepared_account):
    response = client.post(
        f"/api/accounts/{prepared_account.id}/tasks",
        json={"course_ids": ["math"]},
    )
    assert response.status_code == 201
    return response.get_json()["data"]["id"]


def test_start_two_accounts_and_read_isolated_logs(client, prepared_accounts, fake_runner):
    first, second = prepared_accounts
    first_response = client.post(f"/api/accounts/{first.id}/tasks", json={"course_ids": ["math"]})
    second_response = client.post(f"/api/accounts/{second.id}/tasks", json={"course_ids": ["english"]})
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    first_task = first_response.get_json()["data"]["id"]
    second_task = second_response.get_json()["data"]["id"]
    fake_runner.log(first_task, "first-only")
    fake_runner.log(second_task, "second-only")
    assert [item["message"] for item in client.get(f"/api/tasks/{first_task}/logs?after=0").get_json()["data"]["items"]] == ["first-only"]
    assert [item["message"] for item in client.get(f"/api/tasks/{second_task}/logs?after=0").get_json()["data"]["items"]] == ["second-only"]


@pytest.mark.parametrize(
    ("setup", "expected_code"),
    [
        ("same_account_active", "account_active"),
        ("global_limit_reached", "task_limit_reached"),
        ("account_disabled", "account_disabled"),
        ("answer_not_tested", "answer_not_ready"),
    ],
)
def test_start_conflicts_return_stable_codes(client, store, prepared_accounts, fake_runner, setup, expected_code):
    first, second = prepared_accounts
    if setup == "same_account_active":
        response = client.post(f"/api/accounts/{first.id}/tasks", json={"course_ids": ["math"]})
        assert response.status_code == 201
        account_id = first.id
    elif setup == "global_limit_reached":
        manager = client.application.extensions["task_manager"]
        manager.max_active_accounts = 1
        manager._active_slots = threading.BoundedSemaphore(1)
        response = client.post(f"/api/accounts/{first.id}/tasks", json={"course_ids": ["math"]})
        assert response.status_code == 201
        account_id = second.id
    elif setup == "account_disabled":
        store.set_account_enabled(first.id, False)
        account_id = first.id
    else:
        store.set_account_enabled(first.id, True)
        store.save_preferences(first.id, AccountPreferences(answer_enabled=True, selected_course_ids=["math"]))
        store.save_answer_connection(enabled=True, api_key="answer-key")
        account_id = first.id

    response = client.post(f"/api/accounts/{account_id}/tasks", json={"course_ids": ["math"]})
    assert response.status_code == 409
    assert response.get_json()["code"] == expected_code


def test_missing_courses_and_invalid_cursor_are_rejected(client, prepared_account, running_task):
    no_courses = client.post(f"/api/accounts/{prepared_account.id}/tasks", json={"course_ids": []})
    assert no_courses.status_code == 400
    assert no_courses.get_json()["code"] == "courses_required"
    bad_cursor = client.get(f"/api/tasks/{running_task}/logs?after=not-a-number")
    assert bad_cursor.status_code == 400
    assert bad_cursor.get_json()["code"] == "invalid_cursor"


def test_cancel_is_idempotent_and_unknown_task_is_404(client, running_task):
    assert client.post(f"/api/tasks/{running_task}/cancel").get_json()["data"]["state"] == "stopping"
    assert client.post(f"/api/tasks/{running_task}/cancel").get_json()["data"]["state"] == "stopping"
    missing = client.get("/api/tasks/missing")
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "task_not_found"


def test_cancel_terminal_task_returns_task_not_running(client, app, running_task):
    manager = app.extensions["task_manager"]
    runner = app.extensions["task_manager"].runner
    runner.release(running_task)
    assert manager.wait(running_task, timeout=1)
    response = client.post(f"/api/tasks/{running_task}/cancel")
    assert response.status_code == 409
    assert response.get_json()["code"] == "task_not_running"


def test_task_reads_are_public_and_never_return_secrets(client, running_task):
    responses = [
        client.get("/api/tasks"),
        client.get(f"/api/tasks/{running_task}"),
        client.get(f"/api/tasks/{running_task}/details"),
    ]
    for response in responses:
        text = response.get_data(as_text=True).lower()
        assert response.status_code == 200
        assert "password" not in text
        assert "cookies" not in text
        assert "api_key" not in text
        assert "authorization" not in text


@pytest.mark.parametrize(
    "method_and_path",
    [
        ("put", "/api/settings/runtime"),
        ("put", "/api/settings/answer-connection"),
        ("delete", "/api/settings/answer-connection/key"),
    ],
)
def test_shared_setting_mutations_are_blocked_while_tasks_run(
    client, running_task, method_and_path
):
    method, path = method_and_path
    if method == "put":
        response = getattr(client, method)(path, json={})
    else:
        response = getattr(client, method)(path)
    assert response.status_code == 409
    assert response.get_json()["code"] == "settings_in_use"


def test_draft_connection_test_remains_available_while_task_runs(
    client, running_task
):
    response = client.post(
        "/api/settings/answer-connection/test",
        json={"base_url": "http://localhost:8849/v1", "api_key": "draft-key"},
    )
    # The default transport is unavailable in this test process, but the
    # request must reach the connection service rather than settings_in_use.
    assert response.status_code != 409


def test_answer_enabled_task_requires_a_successful_probe_of_saved_connection(
    tmp_path, fake_runner
):
    import httpx

    def handler(_request):
        return httpx.Response(200, json={"data": [{"id": "model"}]})

    manager = TaskManager(runner=fake_runner, max_active_accounts=1)
    application = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "TASK_MANAGER": manager,
            "ANSWER_CONNECTION_TRANSPORT": httpx.MockTransport(handler),
        }
    )
    client = application.test_client()
    store = application.extensions["services"]["store"]
    account = store.create_account("账号", "100", "password")
    store.save_preferences(
        account.id,
        AccountPreferences(selected_course_ids=["math"], answer_enabled=True),
    )
    client.put(
        "/api/settings/answer-connection",
        json={"enabled": True, "model": "model", "api_key": "saved-key"},
    )
    blocked = client.post(
        f"/api/accounts/{account.id}/tasks", json={"course_ids": ["math"]}
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "answer_not_ready"
    assert client.post("/api/settings/answer-connection/test", json={}).status_code == 200
    started = client.post(
        f"/api/accounts/{account.id}/tasks", json={"course_ids": ["math"]}
    )
    assert started.status_code == 201


def test_start_uses_saved_courses_when_omitted_and_persists_explicit_selection(
    client, app, prepared_account
):
    store = app.extensions["services"]["store"]
    store.save_preferences(
        prepared_account.id,
        AccountPreferences(selected_course_ids=["saved-course"]),
    )
    manager = app.extensions["task_manager"]
    saved = client.post(f"/api/accounts/{prepared_account.id}/tasks", json={})
    assert saved.status_code == 201
    saved_id = saved.get_json()["data"]["id"]
    assert manager.get_context(saved_id).course_ids == ["saved-course"]
    app.extensions["task_manager"].runner.release(saved_id)
    manager.wait(saved_id, timeout=1)

    explicit = client.post(
        f"/api/accounts/{prepared_account.id}/tasks",
        json={"course_ids": ["new-course"]},
    )
    assert explicit.status_code == 201
    explicit_id = explicit.get_json()["data"]["id"]
    assert manager.get_context(explicit_id).course_ids == ["new-course"]
    assert store.get_preferences(prepared_account.id).selected_course_ids == [
        "new-course"
    ]


def test_account_mutation_uses_real_manager_active_guard(
    client, prepared_account, app
):
    response = client.post(
        f"/api/accounts/{prepared_account.id}/tasks", json={"course_ids": ["math"]}
    )
    task_id = response.get_json()["data"]["id"]
    blocked = client.patch(
        f"/api/accounts/{prepared_account.id}", json={"name": "blocked"}
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "account_active"
    app.extensions["task_manager"].runner.release(task_id)


def test_task_context_receives_answer_service_semaphore(client, app, prepared_account):
    class AnswerService:
        def __init__(self):
            self.semaphore = threading.BoundedSemaphore(1)

        def get_semaphore(self):
            return self.semaphore

    answer_service = AnswerService()
    app.extensions["services"]["answer_connection_service"] = answer_service
    app.extensions["services"]["answer_connection"] = answer_service
    app.extensions["services"]["answer_service"] = answer_service
    response = client.post(
        f"/api/accounts/{prepared_account.id}/tasks", json={"course_ids": ["math"]}
    )
    assert response.status_code == 201
    task_id = response.get_json()["data"]["id"]
    assert app.extensions["task_manager"].get_context(task_id).answer_semaphore is answer_service.semaphore
