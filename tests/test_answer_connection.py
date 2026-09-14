from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from webapp.answer_connection import (
    AnswerConnectionDraft,
    AnswerConnectionService,
    normalize_completion_url,
    outbound_url,
)
from webapp import create_app
from webapp.crypto import SecretBox
from webapp.store import SQLiteStore
from api.answer import AI


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (
            "http://localhost:8849/v1",
            "http://localhost:8849/v1/chat/completions",
        ),
        (
            "http://localhost:8849/v1/",
            "http://localhost:8849/v1/chat/completions",
        ),
        (
            "http://localhost:8849/v1/chat/completions",
            "http://localhost:8849/v1/chat/completions",
        ),
    ],
)
def test_normalize_completion_url(base, expected):
    assert normalize_completion_url(base) == expected


def test_docker_outbound_url_rewrites_only_loopback_host():
    assert (
        outbound_url("http://localhost:8849/v1", True)
        == "http://host.docker.internal:8849/v1"
    )
    assert (
        outbound_url("http://192.168.1.8:8849/v1", True)
        == "http://192.168.1.8:8849/v1"
    )
    assert outbound_url("http://localhost:8849/v1", False) == "http://localhost:8849/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://localhost:8849/v1",
        "localhost:8849/v1",
        "http://user:password@localhost:8849/v1",
        "http://localhost:8849/v1?token=secret",
        "http://localhost:8849/v1#fragment",
    ],
)
def test_answer_url_rejects_unsafe_forms(base_url):
    with pytest.raises(ValueError):
        normalize_completion_url(base_url)


def test_connection_service_checks_models_with_draft_key(tmp_path: Path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"data": [{"id": "gemini-3.8-flash-high"}]},
        )

    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    service = AnswerConnectionService(
        store,
        transport=httpx.MockTransport(handler),
    )
    result = service.test(
        AnswerConnectionDraft(
            base_url="http://localhost:8849/v1",
            model="gemini-3.8-flash-high",
            api_key="draft-only-key",
        )
    )

    assert result.ok is True
    assert result.model_found is True
    assert seen == {
        "authorization": "Bearer draft-only-key",
        "url": "http://localhost:8849/v1/models",
    }


def test_connection_service_maps_missing_model_and_timeout(tmp_path: Path):
    def missing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "other-model"}]})

    store = SQLiteStore(tmp_path / "missing.sqlite3", SecretBox(tmp_path))
    service = AnswerConnectionService(
        store,
        transport=httpx.MockTransport(missing_handler),
    )
    result = service.test(
        AnswerConnectionDraft(
            base_url="http://localhost:8849/v1",
            model="wanted-model",
            api_key="key",
        )
    )
    assert result.ok is False
    assert result.model_found is False
    assert result.code == "answer_model_missing"


def test_connection_routes_mask_and_preserve_key(tmp_path: Path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    client = app.test_client()
    client.put(
        "/api/settings/answer-connection",
        json={
            "base_url": "http://localhost:8849/v1",
            "model": "gemini-3.8-flash-high",
            "api_key": "route-secret-key",
        },
    )

    read = client.get("/api/settings/answer-connection")
    body = read.get_json()
    body_text = read.get_data(as_text=True)
    assert read.status_code == 200
    assert body["data"]["has_api_key"] is True
    assert "api_key" not in body["data"]
    assert "route-secret-key" not in body_text

    client.put(
        "/api/settings/answer-connection",
        json={"model": "updated-model"},
    )
    resolved = app.extensions["services"]["store"].resolve_answer_connection()
    assert resolved.api_key == "route-secret-key"
    assert resolved.model == "updated-model"

    cleared = client.delete("/api/settings/answer-connection/key")
    assert cleared.status_code == 200
    assert cleared.get_json()["data"]["has_api_key"] is False
    assert app.extensions["services"]["store"].resolve_answer_connection().api_key is None


def test_connection_test_route_uses_draft_key_without_returning_it(tmp_path: Path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"data": [{"id": "gemini-3.8-flash-high"}]},
        )

    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "ANSWER_CONNECTION_TRANSPORT": httpx.MockTransport(handler),
        }
    )
    response = app.test_client().post(
        "/api/settings/answer-connection/test",
        json={
            "base_url": "http://localhost:8849/v1",
            "model": "gemini-3.8-flash-high",
            "api_key": "request-only-secret",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["ok"] is True
    assert response.get_json()["data"]["model_found"] is True
    assert "request-only-secret" not in response.get_data(as_text=True)
    assert seen["authorization"] == "Bearer request-only-secret"


def test_connection_test_route_reuses_saved_key_when_omitted(tmp_path: Path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"data": [{"id": "saved-model"}]},
        )

    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "ANSWER_CONNECTION_TRANSPORT": httpx.MockTransport(handler),
        }
    )
    client = app.test_client()
    assert client.put(
        "/api/settings/answer-connection",
        json={"model": "saved-model", "api_key": "stored-only-secret"},
    ).status_code == 200
    response = client.post(
        "/api/settings/answer-connection/test",
        json={"model": "saved-model"},
    )
    assert response.status_code == 200
    assert seen["authorization"] == "Bearer stored-only-secret"
    assert "stored-only-secret" not in response.get_data(as_text=True)


def test_service_owns_one_shared_bounded_semaphore(tmp_path: Path):
    store = SQLiteStore(tmp_path / "app.sqlite3", SecretBox(tmp_path))
    store.save_answer_connection(max_concurrency=2)
    service = AnswerConnectionService(store)
    semaphore = service.get_semaphore()
    assert isinstance(semaphore, __import__("threading").BoundedSemaphore)
    assert semaphore is service.get_semaphore()


def test_injected_ai_semaphore_gates_simultaneous_provider_requests():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"Answer": ["A"]}'}},
                ]
            },
        )

    semaphore = threading.BoundedSemaphore(1)
    providers = [AI(request_semaphore=semaphore), AI(request_semaphore=semaphore)]
    for provider in providers:
        provider._httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
        provider.key = "test-key"
        provider.model = "test-model"
        provider.endpoint = "http://answer.invalid/v1/chat/completions"
        provider.min_interval_seconds = 0
        provider.max_retries = 1
        provider.retry_delay = 0

    def invoke(provider: AI):
        return provider._invoke_completion(
            [{"role": "user", "content": "question"}]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, providers[0])
        assert first_entered.wait(2)
        second = executor.submit(invoke, providers[1])
        assert not second_entered.wait(0.1)
        release_first.set()
        assert first.result(timeout=2) == "A"
        assert second.result(timeout=2) == "A"
    for provider in providers:
        provider._httpx_client.close()
