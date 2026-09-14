# Apple-Style Multi-Account Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the local Web app as a desktop-first macOS-style workbench that safely saves multiple Chaoxing accounts, runs different accounts concurrently, isolates sessions/tasks/logs, and uses one shared OpenAI-compatible answer service.

**Architecture:** A Flask application factory composes a SQLite/Fernet persistence layer, per-account Chaoxing sessions, a lock-protected in-process task manager, task-scoped logging, and blueprinted JSON APIs. A React Router frontend consumes those APIs through a macOS-style application shell. A multi-stage Docker image builds `web/dist` and runs one Gunicorn worker with threads so process-local task state remains coherent.

**Tech Stack:** Python 3.13, Flask 3.1, sqlite3, cryptography/Fernet, requests/httpx, Loguru, pytest; React 18, React Router, Radix Dialog, Tailwind CSS, Vite, Vitest, Testing Library; Docker Compose and Gunicorn.

**Spec:** `docs/superpowers/specs/2026-09-14-apple-multi-account-web-design.md`

## Global Constraints

- Keep CLI startup through `main.py` working after the HTTP-session refactor.
- Use one independent `requests.Session` and cookie jar per Chaoxing account; no singleton session remains.
- At any time, one account has zero or one active task; different accounts may run concurrently.
- Keep task state process-local and run Gunicorn with exactly one worker.
- Persist data under `./data` outside Docker and `/app/data` in Docker.
- Encrypt passwords, cookies, and answer API keys with a persistent local Fernet key; never return or log plaintext secrets.
- Treat `http://localhost:8849/v1` as the user-visible answer-service base URL and translate loopback to `host.docker.internal` only for outbound requests made inside Docker.
- Use `gemini-3.8-flash-high` as the initial configured model, but never commit the supplied API key.
- Bind the Docker Web service as `127.0.0.1:5001:5000` and expose container port `5000`.
- Build `web/dist` inside the Docker image; keep it ignored and uncommitted.
- Use the system font stack, flat content surfaces, restrained navigation material, one blue accent, and no gradients, purple accents, glows, decorative animation, or excessive floating cards.
- Keep interaction feedback at 200 ms or less, respect `prefers-reduced-motion`, use `min-h-dvh`, and preserve visible keyboard focus.
- Use the existing `{status, data}` / `{status, msg, code}` response envelope.

## File Structure

### Backend files

- `app.py` — becomes a thin WSGI entry point after the replacement API is complete in Task 8.
- `webapp/__init__.py` — `create_app`, configuration defaults, service composition, and blueprint registration.
- `webapp/models.py` — immutable account/settings value objects and task/log state types.
- `webapp/crypto.py` — persistent Fernet key creation and authenticated secret encryption.
- `webapp/store.py` — SQLite schema and typed account/preferences/settings operations.
- `webapp/account_service.py` — account verification, cookie persistence, and course retrieval.
- `webapp/answer_connection.py` — URL validation/normalization, Docker host translation, connection testing, and shared concurrency gate.
- `webapp/task_manager.py` — per-account task admission, state transitions, cancellation events, snapshots, and bounded logs.
- `webapp/task_logging.py` — Loguru task context helpers and one task-routing sink.
- `webapp/study_runner.py` — adapter between `TaskManager` and the existing `main.py` learning engine.
- `webapp/routes/accounts.py` — account CRUD, verify, courses, and preferences endpoints.
- `webapp/routes/settings.py` — answer-connection and runtime settings endpoints.
- `webapp/routes/tasks.py` — task list/start/status/details/logs/cancel endpoints.
- `webapp/routes/static.py` — SPA/static fallback that never intercepts `/api`.
- `api/base.py` — replace the singleton session with a Chaoxing-instance session.
- `api/cookies.py` — explicit cookie serialization/file compatibility helpers.
- `api/live.py` — receive the owning Chaoxing session explicitly.
- `main.py` — propagate task ID, cancellation, reporter, answer semaphore, and logging context into workers.

### Backend tests

- `requirements-dev.txt` — pytest and test-only dependencies.
- `tests/conftest.py` — temporary app/data fixtures.
- `tests/test_app_factory.py` — health and JSON error envelope.
- `tests/test_account_sessions.py` — per-account session/cookie and CLI compatibility regression tests.
- `tests/test_crypto_store.py` — encryption, SQLite schema, account isolation, and redaction data fixtures.
- `tests/test_account_routes.py` — account CRUD, validation, course, preference, and conflict API tests.
- `tests/test_answer_connection.py` — URL normalization, host translation, key masking, model test, and shared concurrency tests.
- `tests/test_answer_cache.py` — process-wide locking and atomic writes for the shared answer cache.
- `tests/test_task_manager.py` — one-task-per-account, parallel accounts, cancellation, state, and log isolation tests.
- `tests/test_study_runner.py` — runner callbacks, cancellation boundaries, and answer-gate injection.
- `tests/test_task_routes.py` — task endpoint and cursor-log integration tests.

### Frontend files

- `web/src/App.jsx` — route tree, empty-account redirect, and global loading/error boundary.
- `web/src/api/client.js` — Axios instance and normalized API error helper.
- `web/src/api/accounts.js`, `tasks.js`, `settings.js` — endpoint-specific request functions.
- `web/src/hooks/usePolling.js` — terminal-state-aware two-second polling.
- `web/src/components/shell/AppShell.jsx`, `Titlebar.jsx`, `AccountSidebar.jsx` — persistent macOS-style chrome.
- `web/src/components/accounts/AccountDialog.jsx` — accessible add/edit/verify account dialog.
- `web/src/components/tasks/TaskStatus.jsx`, `TaskProgress.jsx`, `TaskLog.jsx` — shared task presentation.
- `web/src/components/ui/Alert.jsx`, `Button.jsx`, `Field.jsx`, `Input.jsx`, `Progress.jsx`, `StatusDot.jsx` — focused accessible primitives.
- `web/src/pages/OverviewPage.jsx` — all-account task overview.
- `web/src/pages/LaunchPage.jsx` — course-first selector and launch inspector.
- `web/src/pages/TaskPage.jsx` — account-specific task monitor.
- `web/src/pages/SettingsPage.jsx` — answer connection, runtime, notification, and OCR settings.
- `web/src/index.css` and `web/tailwind.config.js` — Apple-inspired tokens, material layer, typography, focus, responsive rules, and reduced motion.
- `web/src/test/setup.js` plus colocated `*.test.jsx` files — Vitest/Testing Library coverage.

### Deployment files

- `Dockerfile` — Node build stage plus Python/Gunicorn runtime.
- `compose.yaml` — localhost-only port, persistent volume, host gateway, and health check.
- `.dockerignore` — exclude caches, secrets, generated files, and local data.
- `.env.example` — non-secret deployment defaults only.
- `README.md` — new local and Docker instructions, port table, API-host note, and secret-handling warning.

### Test-double contracts

- `tests/conftest.py` exposes `app_factory(**service_overrides)`, `app`, `client`, `store`, and `saved_account`; every app uses `tmp_path / "data"` and removes its Loguru task sink during teardown.
- `tests/test_account_sessions.py` defines `FakeSession` with `cookies`, `requested_urls`, `get`, `post`, and `request`; it never makes a network request.
- `tests/test_account_routes.py` defines `FakeAccountService` with `verify`, `get_courses`, `fail_verification`, and `fetch_count`, plus `FakeTaskGuard.active_account_ids`.
- `tests/test_answer_connection.py` constructs `httpx.Client(transport=httpx.MockTransport(handler))`; the handler records method, URL, and sanitized headers.
- `tests/test_task_manager.py` defines `BlockingRunner`, `ControllableRunner`, and `OutcomeRunner`; each stores received `StudyRunContext` objects and uses events instead of sleep-based synchronization.
- `tests/test_study_runner.py` defines `FakeEngineFactory` and `FakeReporter`; the factory records auth/config inputs and invokes deterministic course/chapter/video callbacks.
- `tests/test_task_routes.py` injects the real `TaskManager` with a controllable fake runner through `app_factory`.
- Frontend tests mock only named functions from `src/api/accounts.js`, `tasks.js`, and `settings.js`; `renderPage(path)` wraps the component in `MemoryRouter`, and `currentLocation()` reads a test-only location probe.

---

### Task 1: Establish the Flask Application Factory and Test Harness

**Files:**
- Create: `webapp/__init__.py`
- Create: `webapp/routes/__init__.py`
- Create: `webapp/routes/static.py`
- Create: `tests/conftest.py`
- Create: `tests/test_app_factory.py`
- Create: `requirements-dev.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `create_app(test_config: Mapping[str, Any] | None = None) -> Flask`
- Produces: Flask config keys `DATA_DIR: Path`, `DATABASE_PATH: Path`, `RUNNING_IN_DOCKER: bool`, and `TESTING: bool`
- Produces: `GET /api/health -> {"status": true, "data": {"service": "chaoxing-web"}}`

- [ ] **Step 1: Add pytest dependencies and the failing factory tests**

```text
# requirements-dev.txt
-r requirements.txt
pytest>=8.4,<9
pytest-cov>=6.2,<7
pytest-repeat>=0.9.4,<1
```

```python
# tests/test_app_factory.py
from pathlib import Path

from webapp import create_app


def test_health_uses_standard_envelope(tmp_path: Path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "status": True,
        "data": {"service": "chaoxing-web"},
    }


def test_unknown_api_route_returns_json(tmp_path: Path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    response = app.test_client().get("/api/not-real")
    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"
```

- [ ] **Step 2: Run the focused tests and verify the factory is missing**

Run: `python -m pytest tests/test_app_factory.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'webapp'`.

- [ ] **Step 3: Implement the minimal application factory without replacing the running legacy app yet**

```python
# webapp/__init__.py
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    data_dir = Path("data").resolve()
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE_PATH=data_dir / "chaoxing-web.sqlite3",
        RUNNING_IN_DOCKER=False,
    )
    if test_config:
        app.config.update(test_config)
    app.config["DATA_DIR"] = Path(app.config["DATA_DIR"])
    app.config["DATABASE_PATH"] = app.config["DATA_DIR"] / "chaoxing-web.sqlite3"
    app.config["DATA_DIR"].mkdir(parents=True, exist_ok=True)

    @app.get("/api/health")
    def health():
        return jsonify(status=True, data={"service": "chaoxing-web"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(status=False, msg="Not Found", code="not_found"), 404

    return app
```

Add this pytest configuration to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 4: Run the factory tests and the import smoke check**

Run: `python -m pytest tests/test_app_factory.py -v`

Expected: 2 passed.

Run: `python -c "from webapp import create_app; print(create_app().url_map)"`

Expected: output includes `/api/health` and no import exception.

- [ ] **Step 5: Commit the factory foundation**

```bash
git add pyproject.toml requirements-dev.txt webapp tests/conftest.py tests/test_app_factory.py
git commit -m "refactor: add web application factory"
```

### Task 2: Isolate Chaoxing HTTP Sessions and Cookies per Account

**Files:**
- Modify: `api/base.py`
- Modify: `api/cookies.py`
- Modify: `api/live.py`
- Modify: `main.py`
- Create: `tests/test_account_sessions.py`

**Interfaces:**
- Produces: `build_session(initial_cookies: Mapping[str, str] | None = None) -> requests.Session`
- Produces: `Chaoxing(account, tiku=None, *, session: requests.Session | None = None, cookie_update_callback: Callable[[dict[str, str]], None] | None = None, **kwargs)`
- Produces: `load_cookie_file(path: str | Path = gc.COOKIES_PATH) -> dict[str, str]`
- Produces: `save_cookie_file(cookies: Mapping[str, str], path: str | Path = gc.COOKIES_PATH) -> None`
- Produces: `Live(attachment: dict, defaults: dict, course_id: str, session: requests.Session)`
- Preserves: `main.init_chaoxing(common_config, tiku_config)` for CLI callers

- [ ] **Step 1: Write failing tests for independent sessions and explicit live-session use**

```python
# tests/test_account_sessions.py
from api.base import Account, Chaoxing
from api.live import Live


def test_chaoxing_instances_do_not_share_cookie_jars():
    first = Chaoxing(Account("100", "pw-a"))
    second = Chaoxing(Account("200", "pw-b"))
    first.session.cookies.set("_uid", "account-a")
    second.session.cookies.set("_uid", "account-b")
    assert first.session is not second.session
    assert first.session.cookies.get("_uid") == "account-a"
    assert second.session.cookies.get("_uid") == "account-b"


def test_live_uses_the_session_owned_by_its_account(fake_session):
    live = Live(
        attachment={"property": {"streamName": "s", "vdoid": "v"}},
        defaults={"userid": "u"},
        course_id="c",
        session=fake_session,
    )
    live.do_finish()
    assert fake_session.requested_urls[0].startswith("https://zhibo.chaoxing.com/")
```

Add a cookie-file round-trip test that writes two keys to `tmp_path / "cookies.txt"`, reloads them, and asserts the mapping is identical.

- [ ] **Step 2: Run the session tests and verify singleton coupling fails them**

Run: `python -m pytest tests/test_account_sessions.py -v`

Expected: FAIL because `Chaoxing` has no `session` attribute and `Live` rejects the `session` argument.

- [ ] **Step 3: Replace singleton calls with instance-owned session access**

Implement `build_session` in `api/base.py`, initialize `self.session`, and replace every `SessionManager.get_session()` / `get_instance()._session` use with `self.session`. Password login must use the existing instance rather than a new local `requests.Session`.

```python
def build_session(initial_cookies=None):
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=10))
    session.mount("http://", HTTPAdapter(max_retries=10))
    session.request = functools.partial(session.request, timeout=5)
    session.headers.clear()
    session.headers.update(gc.HEADERS)
    if initial_cookies:
        session.cookies.update(dict(initial_cookies))
    return session
```

After a successful password or cookie login, invoke `cookie_update_callback(self.session.cookies.get_dict())` when configured. Do not log the cookie dictionary.

Update `Live` to store the passed session and use it in `do_finish` and `get_status`. Update the `Live(attachment, defaults, course_id, session=chaoxing.session)` construction in `main.py`.

Keep CLI cookie behavior in `main.init_chaoxing`:

```python
initial_cookies = load_cookie_file()
session = build_session(initial_cookies)
chaoxing = Chaoxing(
    account=account,
    tiku=tiku,
    query_delay=query_delay,
    ai_concurrency=ai_concurrency,
    session=session,
    cookie_update_callback=save_cookie_file,
)
```

- [ ] **Step 4: Prove session isolation and CLI compatibility**

Run: `python -m pytest tests/test_account_sessions.py -v`

Expected: all tests pass.

Run: `python main.py --help`

Expected: existing CLI usage text and exit code 0.

- [ ] **Step 5: Scan for singleton usage and commit**

Run: `rg "SessionManager" api main.py`

Expected: no matches.

```bash
git add api/base.py api/cookies.py api/live.py main.py tests/test_account_sessions.py
git commit -m "refactor: isolate account http sessions"
```

### Task 3: Add Encrypted SQLite Storage for Accounts and Settings

**Files:**
- Create: `webapp/models.py`
- Create: `webapp/crypto.py`
- Create: `webapp/store.py`
- Create: `tests/test_crypto_store.py`
- Modify: `requirements.txt`
- Modify: `webapp/__init__.py`

**Interfaces:**
- Produces: `SecretBox(data_dir: Path)` with `encrypt(value: str) -> str` and `decrypt(token: str) -> str`
- Produces: `SQLiteStore(db_path: Path, secret_box: SecretBox)`
- Produces: `AccountProfile`, `AccountAuth`, `AccountPreferences`, `AnswerConnection`, and `RuntimeSettings` dataclasses
- Produces store methods: `list_accounts`, `get_account`, `get_account_auth`, `create_account`, `update_account`, `delete_account`, `set_account_enabled`, `get_preferences`, `save_preferences`, `get_answer_connection`, `save_answer_connection`, `clear_answer_key`, `get_runtime_settings`, `save_runtime_settings`, and `save_cookies`

- [ ] **Step 1: Write failing encryption and isolation tests**

```python
# tests/test_crypto_store.py
from webapp.crypto import SecretBox
from webapp.models import AccountPreferences
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
```

Add these exact security/default cases:

```python
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
    store.save_answer_connection(base_url="http://localhost:8849/v1", model="gemini-3.8-flash-high", api_key="secret-key")
    store.save_answer_connection(base_url="http://localhost:8849/v1", model="gemini-3.8-flash-high", api_key=None)
    assert store.resolve_answer_connection().api_key == "secret-key"


@pytest.mark.parametrize("limit", [0, 11])
def test_runtime_limit_rejects_values_outside_one_to_ten(limit):
    with pytest.raises(ValueError):
        RuntimeSettings(max_active_accounts=limit)
```

- [ ] **Step 2: Run the storage tests and verify the modules are missing**

Run: `python -m pytest tests/test_crypto_store.py -v`

Expected: FAIL during import because `webapp.crypto`, `webapp.models`, and `webapp.store` do not exist.

- [ ] **Step 3: Define typed models and authenticated encryption**

```python
# webapp/models.py
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AccountProfile:
    id: str
    name: str
    username: str
    enabled: bool
    has_secret: bool
    has_cookies: bool
    verification_status: Literal["unverified", "valid", "invalid"]
    last_verified_at: str | None


@dataclass(frozen=True)
class AccountAuth:
    username: str
    password: str
    cookies: dict[str, str]


@dataclass(frozen=True)
class AccountPreferences:
    selected_course_ids: list[str] = field(default_factory=list)
    speed: float = 1.0
    jobs: int = 4
    notopen_action: Literal["retry", "continue"] = "retry"
    answer_enabled: bool = False
    answer_cover_rate: float = 0.9
    answer_auto_submit: bool = False
    notification_config: dict = field(default_factory=dict)
    ocr_config: dict = field(default_factory=dict)
```

Define `AnswerConnection` with `enabled`, `base_url`, `model`, `has_api_key`, `timeout_seconds`, `max_retries`, and `max_concurrency`. Define `RuntimeSettings(max_active_accounts: int = 3)`.

`SecretBox` generates `data_dir / "secret.key"` once, applies `chmod(0o600)`, and wraps `Fernet.encrypt`/`decrypt`. Add `cryptography>=45,<46` to `requirements.txt`.

- [ ] **Step 4: Implement schema initialization and typed CRUD**

Use one `sqlite3.connect` per store operation, enable `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL`, serialize schema creation with a `threading.Lock`, and store preferences/settings as JSON. Generate account IDs with `uuid.uuid4()` and UTC timestamps with timezone information.

The secret columns contain only Fernet tokens. Public read methods construct `AccountProfile`; only `get_account_auth` and the answer-connection resolver decrypt secret values.

Register `SecretBox` and `SQLiteStore` in `create_app` under `app.extensions["services"]`.

- [ ] **Step 5: Run storage tests and inspect the database for plaintext**

Run: `python -m pytest tests/test_crypto_store.py -v`

Expected: all tests pass.

Run: `python -m pytest tests/test_crypto_store.py::test_account_secrets_are_encrypted_at_rest -v`

Expected: PASS; the assertion reads the SQLite bytes and proves the fixture secret is absent.

- [ ] **Step 6: Commit encrypted persistence**

```bash
git add requirements.txt webapp/__init__.py webapp/models.py webapp/crypto.py webapp/store.py tests/test_crypto_store.py
git commit -m "feat: add encrypted account storage"
```

### Task 4: Implement Account Validation, Course Retrieval, and Account APIs

**Files:**
- Create: `webapp/account_service.py`
- Create: `webapp/routes/accounts.py`
- Create: `tests/test_account_routes.py`
- Modify: `webapp/__init__.py`

**Interfaces:**
- Consumes: `SQLiteStore`, `AccountProfile`, `AccountAuth`, and `AccountPreferences` from Task 3
- Consumes: per-instance `Chaoxing` session API from Task 2
- Produces: `AccountService.verify(account_id: str) -> AccountProfile`
- Produces: `AccountService.get_courses(account_id: str, refresh: bool = False) -> list[dict]`
- Produces: account, verify, courses, and preferences endpoints from the spec

- [ ] **Step 1: Write failing route tests with an injected fake account service**

```python
# tests/test_account_routes.py
def test_create_account_never_returns_password(client):
    response = client.post("/api/accounts", json={
        "name": "张三",
        "username": "13800000000",
        "password": "secret-value",
    })
    body = response.get_json()
    assert response.status_code == 201
    assert body["data"]["name"] == "张三"
    assert body["data"]["has_secret"] is True
    assert "password" not in body["data"]
    assert "secret-value" not in response.get_data(as_text=True)


def test_preferences_do_not_cross_accounts(client, account_ids):
    first, second = account_ids
    client.put(f"/api/accounts/{first}/preferences", json={
        "selected_course_ids": ["course-a"],
        "speed": 1.5,
        "jobs": 4,
        "notopen_action": "retry",
        "answer_enabled": True,
        "answer_cover_rate": 0.9,
        "answer_auto_submit": False,
    })
    assert client.get(f"/api/accounts/{first}/preferences").get_json()["data"]["selected_course_ids"] == ["course-a"]
    assert client.get(f"/api/accounts/{second}/preferences").get_json()["data"]["selected_course_ids"] == []
```

Add these exact mutation, cache, conflict, and range cases:

```python
def test_patch_without_password_preserves_secret(client, store, saved_account):
    response = client.patch(f"/api/accounts/{saved_account.id}", json={"name": "新名称"})
    assert response.status_code == 200
    assert store.get_account_auth(saved_account.id).password == "original-password"


def test_course_refresh_bypasses_per_account_cache(client, fake_account_service, saved_account):
    client.get(f"/api/accounts/{saved_account.id}/courses")
    client.get(f"/api/accounts/{saved_account.id}/courses")
    client.get(f"/api/accounts/{saved_account.id}/courses?refresh=1")
    assert fake_account_service.fetch_count(saved_account.id) == 2


def test_delete_active_account_is_rejected(client, task_guard, saved_account):
    task_guard.active_account_ids.add(saved_account.id)
    response = client.delete(f"/api/accounts/{saved_account.id}")
    assert response.status_code == 409
    assert response.get_json()["code"] == "account_active"


@pytest.mark.parametrize(("field", "value"), [
    ("speed", 0.9),
    ("speed", 2.1),
    ("jobs", 0),
    ("jobs", 11),
    ("answer_cover_rate", -0.1),
    ("answer_cover_rate", 1.1),
])
def test_invalid_preference_ranges_return_400(client, saved_account, field, value):
    payload = default_preferences_payload()
    payload[field] = value
    response = client.put(f"/api/accounts/{saved_account.id}/preferences", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_preferences"
```

```python
def test_list_enable_verify_and_delete(client, saved_account, fake_account_service):
    assert saved_account.id in {item["id"] for item in client.get("/api/accounts").get_json()["data"]}
    disabled = client.patch(f"/api/accounts/{saved_account.id}", json={"enabled": False})
    assert disabled.get_json()["data"]["enabled"] is False
    fake_account_service.fail_verification(saved_account.id, "bad login")
    invalid = client.post(f"/api/accounts/{saved_account.id}/verify")
    assert invalid.status_code == 401
    assert invalid.get_json()["code"] == "account_invalid"
    client.patch(f"/api/accounts/{saved_account.id}", json={"enabled": True})
    assert client.delete(f"/api/accounts/{saved_account.id}").status_code == 204


@pytest.mark.parametrize("suffix", ["", "/verify", "/courses", "/preferences"])
def test_unknown_account_routes_are_stable(client, suffix):
    method = client.put if suffix == "/preferences" else client.post if suffix == "/verify" else client.get
    response = method(f"/api/accounts/missing{suffix}", json=default_preferences_payload() if suffix == "/preferences" else None)
    assert response.status_code == 404
    assert response.get_json()["code"] == "account_not_found"
```

- [ ] **Step 2: Run the route tests and verify endpoints return 404**

Run: `python -m pytest tests/test_account_routes.py -v`

Expected: FAIL because `/api/accounts` and nested account routes are not registered.

- [ ] **Step 3: Implement account-service session creation and cookie updates**

```python
# webapp/account_service.py
class AccountService:
    def __init__(self, store, chaoxing_factory):
        self.store = store
        self.chaoxing_factory = chaoxing_factory

    def _client(self, account_id):
        auth = self.store.get_account_auth(account_id)
        return self.chaoxing_factory(
            auth=auth,
            cookie_update_callback=lambda cookies: self.store.save_cookies(account_id, cookies),
        )

    def verify(self, account_id):
        client = self._client(account_id)
        result = client.login(login_with_cookies=bool(client.session.cookies))
        self.store.record_verification(account_id, valid=bool(result["status"]))
        if not result["status"]:
            raise AccountValidationError(result["msg"])
        return self.store.get_account(account_id)
```

Cache the last successful course list per account in memory within `AccountService`; `refresh=True` bypasses it. Failed refreshes do not erase the last successful cache.

- [ ] **Step 4: Implement blueprinted account routes and validation**

Use `Blueprint("accounts", __name__, url_prefix="/api/accounts")`. Serialize dataclasses with explicit public dictionaries. An omitted `password` in PATCH keeps the encrypted value. Reject blank names/usernames, unknown fields, invalid numeric ranges, and cookie text that cannot be parsed as `name=value` pairs.

Return `409 account_active` from DELETE when the injected `TaskManager.has_active_task(account_id)` is true. Before Task 6 exists, inject a `NoopTaskGuard` whose method returns false; Task 6 replaces it through application composition.

- [ ] **Step 5: Run account tests and the focused security scan**

Run: `python -m pytest tests/test_account_routes.py tests/test_account_sessions.py tests/test_crypto_store.py -v`

Expected: all tests pass.

Run: `rg -n "password.*jsonify|cookies.*jsonify|get_account_auth.*return" webapp/routes webapp/account_service.py`

Expected: no route serializes decrypted credentials or cookies.

- [ ] **Step 6: Commit account management**

```bash
git add webapp/__init__.py webapp/account_service.py webapp/routes/accounts.py tests/test_account_routes.py
git commit -m "feat: add multi-account management api"
```

### Task 5: Add the Shared OpenAI-Compatible Answer Connection

**Files:**
- Create: `webapp/answer_connection.py`
- Create: `webapp/routes/settings.py`
- Create: `tests/test_answer_connection.py`
- Create: `tests/test_answer_cache.py`
- Modify: `webapp/__init__.py`
- Modify: `api/answer.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: encrypted `AnswerConnection` storage from Task 3
- Produces: `normalize_completion_url(base_url: str) -> str`
- Produces: `outbound_url(base_url: str, running_in_docker: bool) -> str`
- Produces: `AnswerConnectionService.test(draft: AnswerConnectionDraft) -> ConnectionTestResult`
- Produces: `AnswerConnectionService.get_semaphore() -> threading.BoundedSemaphore`
- Produces: `main.init_chaoxing(common_config, tiku_config, *, answer_semaphore: threading.Semaphore | None = None)`
- Produces: answer-connection and runtime settings endpoints from the spec

- [ ] **Step 1: Write failing URL, masking, and connection tests**

```python
# tests/test_answer_connection.py
import pytest

from webapp.answer_connection import normalize_completion_url, outbound_url


@pytest.mark.parametrize(("base", "expected"), [
    ("http://localhost:8849/v1", "http://localhost:8849/v1/chat/completions"),
    ("http://localhost:8849/v1/", "http://localhost:8849/v1/chat/completions"),
    ("http://localhost:8849/v1/chat/completions", "http://localhost:8849/v1/chat/completions"),
])
def test_normalize_completion_url(base, expected):
    assert normalize_completion_url(base) == expected


def test_docker_outbound_url_rewrites_only_loopback_host():
    assert outbound_url("http://localhost:8849/v1", True) == "http://host.docker.internal:8849/v1"
    assert outbound_url("http://192.168.1.8:8849/v1", True) == "http://192.168.1.8:8849/v1"
    assert outbound_url("http://localhost:8849/v1", False) == "http://localhost:8849/v1"
```

Add rejection tests for schemes other than HTTP(S), embedded username/password, query strings, fragments, and blank model names. Add route tests proving `GET` returns `has_api_key` but never the key, `PUT` without `api_key` preserves it, Clear Key removes it, and Test Connection uses the draft key without logging it.

Use `httpx.MockTransport` to return:

```json
{"data": [{"id": "gemini-3.8-flash-high"}]}
```

from `/v1/models`, then assert the result is `{ok: true, model_found: true}`.

Add a concurrent cache test:

```python
# tests/test_answer_cache.py
def test_cache_instances_share_a_lock_for_the_same_file(tmp_path):
    cache_path = tmp_path / "answer-cache.json"
    first = CacheDAO(str(cache_path))
    second = CacheDAO(str(cache_path))
    writes = [
        (first, "question-a", "answer-a"),
        (second, "question-b", "answer-b"),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda item: item[0].add_cache(item[1], item[2]), writes))
    assert first.get_cache("question-a") == "answer-a"
    assert first.get_cache("question-b") == "answer-b"
```

- [ ] **Step 2: Run the focused tests and verify the connection module is missing**

Run: `python -m pytest tests/test_answer_connection.py -v`

Expected: FAIL during import because `webapp.answer_connection` does not exist.

- [ ] **Step 3: Implement strict URL handling and Docker translation**

```python
def normalize_completion_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Answer API URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Answer API URL must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
```

`outbound_url` rebuilds the parsed netloc with `host.docker.internal` only when `running_in_docker` is true and the hostname is `localhost` or `127.0.0.1`; preserve scheme, explicit port, and path.

- [ ] **Step 4: Implement connection testing, storage routes, and the shared semaphore**

`AnswerConnectionService.test` first requests `models_url(draft.base_url)` with `Authorization: Bearer` plus the decrypted draft key. If `/models` returns 404 or 405, send a minimal non-streaming completion to the normalized completion URL with `max_tokens: 1`; any 2xx response with a valid OpenAI-style JSON body counts as reachable. A 401/403 maps to `answer_auth_failed`, timeout maps to `answer_timeout`, missing configured model maps to `answer_model_missing`, and other non-2xx responses map to `answer_unavailable`.

Implement endpoints:

```text
GET    /api/settings/answer-connection
PUT    /api/settings/answer-connection
DELETE /api/settings/answer-connection/key
POST   /api/settings/answer-connection/test
GET    /api/settings/runtime
PUT    /api/settings/runtime
```

The test endpoint accepts a draft body; an omitted `api_key` uses the saved key. Do not include request headers or response bodies containing secrets in exceptions returned to the browser. `AnswerConnectionService` owns one `threading.BoundedSemaphore(max_concurrency)` and exposes it through `get_semaphore()` so every Web AI provider receives the same object.

- [ ] **Step 5: Inject one shared answer semaphore into all AI providers**

Add `request_semaphore` to `AI.__init__` or a setter invoked before `_init_tiku`. In `_invoke_completion`, prefer the injected semaphore and fall back to the provider-local semaphore for CLI use.

Extend `main.init_chaoxing`:

```python
def init_chaoxing(common_config, tiku_config, *, answer_semaphore=None):
    tiku = Tiku()
    tiku.config_set(tiku_config)
    tiku = tiku.get_tiku_from_config()
    if isinstance(tiku, AI) and answer_semaphore is not None:
        tiku.set_request_semaphore(answer_semaphore)
    tiku.init_tiku()
    return Chaoxing(account=account, tiku=tiku, query_delay=query_delay)
```

Keep the two-positional-argument call valid for CLI and existing callers.

Make `CacheDAO` use a class-level lock registry keyed by `cache_file.resolve()`. `Tiku.init_tiku` creates one cache using `tiku_config["cache_file"]` when provided, and `Tiku.query` reuses it rather than constructing a new `CacheDAO` per question. CLI retains `cache.json`; the Web runner supplies `data/answer-cache.json` in Task 7.

- [ ] **Step 6: Run answer, storage, and CLI regression tests**

Run: `python -m pytest tests/test_answer_connection.py tests/test_answer_cache.py tests/test_crypto_store.py tests/test_account_sessions.py -v`

Expected: all tests pass.

Run: `python main.py --help`

Expected: exit code 0.

- [ ] **Step 7: Commit the shared answer connection**

```bash
git add webapp/__init__.py webapp/answer_connection.py webapp/routes/settings.py api/answer.py main.py tests/test_answer_connection.py tests/test_answer_cache.py
git commit -m "feat: add shared answer api connection"
```

### Task 6: Build the Concurrent Task Manager and Task-Scoped Log Store

**Files:**
- Create: `webapp/task_manager.py`
- Create: `webapp/task_logging.py`
- Create: `tests/test_task_manager.py`
- Modify: `webapp/models.py`
- Modify: `webapp/__init__.py`

**Interfaces:**
- Consumes: account IDs and runtime limit from Tasks 3–4
- Produces: `TaskState = Literal["running", "stopping", "completed", "failed", "stopped"]`
- Produces: `TaskSnapshot`, `TaskDetails`, `TaskLogEntry`, `StudyRunContext`, and `TaskReporter`
- Produces: `TaskManager.start(account_id, course_ids, preferences, auth, answer) -> TaskSnapshot`
- Produces: `TaskManager.cancel(task_id) -> TaskSnapshot`
- Produces: `TaskManager.list_tasks()`, `get_snapshot`, `get_details`, `get_logs(after)`, and `has_active_task`
- Produces: `run_with_task_context(task_id: str, target: Callable, *args, **kwargs)`

- [ ] **Step 1: Write failing parallel-account and same-account conflict tests**

```python
# tests/test_task_manager.py
import threading

import pytest

from webapp.task_manager import AccountTaskConflict, TaskManager


class BlockingRunner:
    def __init__(self):
        self.started = {}
        self.release = threading.Event()

    def run(self, context):
        self.started[context.account_id] = threading.current_thread().name
        context.reporter.set_current(course="Course", chapter="Chapter")
        self.release.wait(timeout=2)


def test_different_accounts_run_concurrently(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=3)
    first = manager.start(**task_inputs("account-a"))
    second = manager.start(**task_inputs("account-b"))
    assert first.account_id == "account-a"
    assert second.account_id == "account-b"
    assert set(runner.started) == {"account-a", "account-b"}
    runner.release.set()


def test_same_account_cannot_start_twice(task_inputs):
    runner = BlockingRunner()
    manager = TaskManager(runner=runner, max_active_accounts=3)
    manager.start(**task_inputs("account-a"))
    with pytest.raises(AccountTaskConflict):
        manager.start(**task_inputs("account-a"))
    runner.release.set()
```

Add these exact admission, cancellation, and terminal-state cases:

```python
def test_global_account_limit_is_released_after_terminal_task(task_inputs):
    runner = ControllableRunner()
    manager = TaskManager(runner=runner, max_active_accounts=1)
    first = manager.start(**task_inputs("account-a"))
    with pytest.raises(TaskCapacityReached):
        manager.start(**task_inputs("account-b"))
    runner.complete(first.id)
    manager.wait(first.id, timeout=1)
    second = manager.start(**task_inputs("account-b"))
    assert second.state == "running"
    runner.complete(second.id)


def test_cancel_delivers_event_and_reaches_stopped(task_inputs):
    runner = ControllableRunner()
    manager = TaskManager(runner=runner, max_active_accounts=2)
    task = manager.start(**task_inputs("account-a"))
    assert manager.cancel(task.id).state == "stopping"
    assert runner.contexts[task.id].cancel_event.is_set()
    runner.acknowledge_cancel(task.id)
    manager.wait(task.id, timeout=1)
    assert manager.get_snapshot(task.id).state == "stopped"


@pytest.mark.parametrize("outcome", ["completed", "failed", "stopped"])
def test_terminal_snapshot_replaces_previous_account_snapshot(task_inputs, outcome):
    runner = OutcomeRunner(outcome)
    manager = TaskManager(runner=runner, max_active_accounts=1)
    task = manager.start(**task_inputs("account-a"))
    manager.wait(task.id, timeout=1)
    assert manager.list_tasks()[0].state == outcome
```

- [ ] **Step 2: Write failing non-destructive log-cursor tests**

```python
def test_logs_are_partitioned_and_cursor_based(manager_with_running_tasks):
    manager, first, second = manager_with_running_tasks
    manager.append_log(first.id, "first-only", "info")
    manager.append_log(second.id, "second-only", "warning")
    first_page = manager.get_logs(first.id, after=0)
    second_page = manager.get_logs(second.id, after=0)
    assert [item.message for item in first_page.items] == ["first-only"]
    assert [item.message for item in second_page.items] == ["second-only"]
    assert manager.get_logs(first.id, after=0).items == first_page.items
    assert manager.get_logs(first.id, after=first_page.next_cursor).items == []
```

Add a bounded-buffer test that appends 5,010 entries and asserts only the newest 5,000 remain with monotonic sequence values.

- [ ] **Step 3: Run the manager tests and verify types are absent**

Run: `python -m pytest tests/test_task_manager.py -v`

Expected: FAIL because `webapp.task_manager` and its task types do not exist.

- [ ] **Step 4: Implement task state, admission, reporter, and cancellation**

`TaskManager` uses an `RLock`, an `active_by_account` mapping, and a `BoundedSemaphore(max_active_accounts)`. Create task IDs with UUIDs, not usernames. Spawn daemon threads named `chaoxing-task-<short-id>`.

The runner receives:

```python
@dataclass(frozen=True)
class StudyRunContext:
    task_id: str
    account_id: str
    course_ids: list[str]
    preferences: AccountPreferences
    auth: AccountAuth
    answer: ResolvedAnswerConnection | None
    answer_semaphore: threading.Semaphore | None
    cancel_event: threading.Event
    reporter: "TaskReporter"
```

`TaskReporter` exposes `set_current`, `set_counts`, `set_courses`, `set_active_jobs`, and `append_log`. All updates reacquire the manager lock and copy caller-owned dictionaries/lists before storing them.

In the thread wrapper, a normal return becomes `completed` unless cancellation is set, in which case it becomes `stopped`. Exceptions become `failed` with a sanitized message. The `finally` block removes the active-account mapping and releases the global slot exactly once.

- [ ] **Step 5: Implement one task-routing Loguru sink**

```python
def run_with_task_context(task_id, target, *args, **kwargs):
    with logger.contextualize(task_id=task_id):
        return target(*args, **kwargs)


def install_task_log_sink(manager):
    return logger.add(
        lambda message: manager.append_log(
            message.record["extra"]["task_id"],
            message.record["message"],
            message.record["level"].name.lower(),
            timestamp=message.record["time"].timestamp(),
        ),
        filter=lambda record: bool(record["extra"].get("task_id")),
        enqueue=True,
    )
```

Install the sink once during `create_app`. Store the sink ID in `app.extensions` and remove it in test teardown so repeated app construction does not duplicate logs.

- [ ] **Step 6: Run manager tests under repeated construction**

Run: `python -m pytest tests/test_task_manager.py tests/test_app_factory.py -v --count=2`

If `pytest-repeat` is not added, run the same command twice without `--count`; expected result is all tests pass both times and no duplicate log entries.

- [ ] **Step 7: Commit the task core**

```bash
git add webapp/__init__.py webapp/models.py webapp/task_manager.py webapp/task_logging.py tests/test_task_manager.py
git commit -m "feat: add concurrent account task manager"
```

### Task 7: Adapt the Learning Engine to Task Context, Cancellation, and Isolated Reporting

**Files:**
- Create: `webapp/study_runner.py`
- Create: `tests/test_study_runner.py`
- Modify: `main.py`
- Modify: `api/live_process.py`
- Modify: `api/vision_ocr.py`
- Modify: `api/decode.py`
- Modify: `api/answer.py`
- Modify: `webapp/__init__.py`

**Interfaces:**
- Consumes: `StudyRunContext` and `TaskReporter` from Task 6
- Consumes: per-account session and shared answer semaphore from Tasks 2 and 5
- Produces: `ChaoxingStudyRunner(data_dir: Path, engine_factory: Callable).run(context: StudyRunContext) -> None`
- Produces: `StudyCancelled` raised only at safe cancellation checkpoints
- Extends existing `common_config` with `task_id`, `cancel_event`, `chapter_start_callback`, `chapter_done_callback`, and `video_progress_callback`

- [ ] **Step 1: Write failing runner tests around a fake Chaoxing engine**

```python
# tests/test_study_runner.py
def test_runner_reports_courses_and_current_chapter(tmp_path, fake_context, fake_engine_factory):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    runner.run(fake_context)
    assert fake_context.reporter.current_updates[-1] == {
        "course": "高等数学",
        "chapter": "第一章",
    }
    assert fake_context.reporter.count_updates[-1]["completed_courses"] == 1


def test_runner_stops_at_next_safe_checkpoint(tmp_path, fake_context, fake_engine_factory):
    fake_context.cancel_event.set()
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    with pytest.raises(StudyCancelled):
        runner.run(fake_context)
    assert fake_engine_factory.processed_courses == []
```

Add these exact propagation and redaction cases:

```python
def test_runner_propagates_account_and_answer_context(tmp_path, fake_context, fake_engine_factory):
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    runner.run(fake_context)
    call = fake_engine_factory.last_call
    assert call.auth.cookies == {"_uid": "account-a"}
    assert call.course_ids == ["math"]
    assert call.answer_semaphore is fake_context.answer_semaphore
    assert call.tiku_config["submit"] == "false"
    assert call.tiku_config["cache_file"].endswith("answer-cache.json")


def test_runner_sanitizes_secret_values_from_failure(tmp_path, fake_context, fake_engine_factory):
    fake_engine_factory.raise_with_message("password-a cookie-a bearer-a")
    runner = ChaoxingStudyRunner(data_dir=tmp_path, engine_factory=fake_engine_factory)
    with pytest.raises(StudyRunError) as captured:
        runner.run(fake_context)
    rendered = str(captured.value)
    assert "password-a" not in rendered
    assert "cookie-a" not in rendered
    assert "bearer-a" not in rendered


def test_parallel_ocr_contexts_do_not_touch_process_environment(ocr_context_runner):
    before = dict(os.environ)
    results = ocr_context_runner.run_two({"provider": "openai"}, {"provider": "claude"})
    assert results == ["openai", "claude"]
    assert dict(os.environ) == before
```

- [ ] **Step 2: Run runner tests and verify the adapter is missing**

Run: `python -m pytest tests/test_study_runner.py -v`

Expected: FAIL during import because `webapp.study_runner` does not exist.

- [ ] **Step 3: Implement the Web runner adapter without duplicating learning logic**

Construct `common_config` from `StudyRunContext`, call `main.init_chaoxing` with the account-specific session/cookie callback and shared answer semaphore, log in, filter courses, and call the existing course processor. Translate the current callbacks into reporter calls.

Build the answer configuration as:

```python
tiku_config = {
    "provider": "AI" if context.preferences.answer_enabled else "",
    "endpoint": normalize_completion_url(context.answer.outbound_base_url),
    "key": context.answer.api_key,
    "model": context.answer.model,
    "cover_rate": context.preferences.answer_cover_rate,
    "submit": str(context.preferences.answer_auto_submit).lower(),
    "timeout": context.answer.timeout_seconds,
    "max_retries": context.answer.max_retries,
}
```

Do not put credentials or the answer configuration into task snapshots or logger messages.

Set `cache_file` to `str(self.data_dir / "answer-cache.json")` in the Web runner's `tiku_config`, where `self.data_dir` is injected when `ChaoxingStudyRunner` is constructed.

- [ ] **Step 4: Add safe cancellation checks to every worker entry path**

Add `raise_if_cancelled(config)` before starting each course, before each chapter, before each job, and after a blocking job returns. Wrap `JobProcessor.worker_thread`, `retry_thread`, `ThreadPoolExecutor` job functions, and live-processing thread entry points by calling `run_with_task_context(config["task_id"], target, *args, **kwargs)`.

`LiveProcessor.run_live` receives an optional cancel event and checks it before each minute update and after the retry delay. A cancellation raises `StudyCancelled`, not `SystemExit`.

Replace process-wide Web OCR mutation with an immutable task context:

```python
@contextmanager
def vision_ocr_context(config: Mapping[str, str] | None):
    token = _vision_ocr_context.set(dict(config) if config else None)
    try:
        yield
    finally:
        _vision_ocr_context.reset(token)
```

`_load_vision_ocr_config` first reads this context and falls back to `CHAOXING_VISION_OCR_*` only when no Web-task context exists. Every worker wrapper re-enters both the task-log context and the same OCR context before calling decoding or answer code.

- [ ] **Step 5: Run runner, task, and CLI regression tests**

Run: `python -m pytest tests/test_study_runner.py tests/test_task_manager.py tests/test_account_sessions.py -v`

Expected: all tests pass.

Run: `python main.py --help`

Expected: exit code 0.

- [ ] **Step 6: Commit the runner integration**

```bash
git add webapp/__init__.py webapp/study_runner.py main.py api/live_process.py api/vision_ocr.py api/decode.py api/answer.py tests/test_study_runner.py
git commit -m "feat: run isolated account learning tasks"
```

### Task 8: Expose Task Start, Monitor, Log, and Cancel APIs

**Files:**
- Create: `webapp/routes/tasks.py`
- Create: `tests/test_task_routes.py`
- Modify: `webapp/__init__.py`
- Modify: `webapp/routes/accounts.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `TaskManager`, `SQLiteStore`, `AccountService`, and `AnswerConnectionService`
- Produces: all task endpoints in the spec
- Produces: stable error codes `account_active`, `task_limit_reached`, `answer_not_ready`, `account_disabled`, `task_not_found`, and `task_not_running`

- [ ] **Step 1: Write failing task-route integration tests**

```python
# tests/test_task_routes.py
def test_start_two_accounts_and_read_isolated_logs(client, prepared_accounts, fake_runner):
    first, second = prepared_accounts
    first_response = client.post(f"/api/accounts/{first}/tasks", json={"course_ids": ["math"]})
    second_response = client.post(f"/api/accounts/{second}/tasks", json={"course_ids": ["english"]})
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    first_task = first_response.get_json()["data"]["id"]
    second_task = second_response.get_json()["data"]["id"]
    fake_runner.log(first_task, "first-only")
    fake_runner.log(second_task, "second-only")
    assert [item["message"] for item in client.get(f"/api/tasks/{first_task}/logs?after=0").get_json()["data"]["items"]] == ["first-only"]
    assert [item["message"] for item in client.get(f"/api/tasks/{second_task}/logs?after=0").get_json()["data"]["items"]] == ["second-only"]
```

Add these exact admission/error cases:

```python
@pytest.mark.parametrize(("setup", "expected_code"), [
    ("same_account_active", "account_active"),
    ("global_limit_reached", "task_limit_reached"),
    ("account_disabled", "account_disabled"),
    ("answer_not_tested", "answer_not_ready"),
])
def test_start_conflicts_return_stable_codes(client, scenario, setup, expected_code):
    account_id = scenario.prepare(setup)
    response = client.post(f"/api/accounts/{account_id}/tasks", json={"course_ids": ["math"]})
    assert response.status_code == 409
    assert response.get_json()["code"] == expected_code


def test_missing_courses_and_invalid_cursor_are_rejected(client, prepared_account, running_task):
    no_courses = client.post(f"/api/accounts/{prepared_account}/tasks", json={"course_ids": []})
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


@pytest.mark.parametrize("mutation", ["runtime_limit", "answer_connection", "clear_answer_key"])
def test_shared_setting_mutations_are_blocked_while_tasks_run(client, scenario, mutation):
    scenario.start_running_task()
    response = scenario.mutate_setting(client, mutation)
    assert response.status_code == 409
    assert response.get_json()["code"] == "settings_in_use"
```

```python
def test_task_reads_are_public_and_draft_connection_test_remains_available(client, running_task):
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
    draft_test = client.post("/api/settings/answer-connection/test", json=answer_connection_draft())
    assert draft_test.status_code == 200
```

Draft connection testing remains allowed while tasks run because it does not mutate the connection used by active tasks.

- [ ] **Step 2: Run the route tests and verify task routes are unavailable**

Run: `python -m pytest tests/test_task_routes.py -v`

Expected: FAIL because `POST /api/accounts/<id>/tasks` and `/api/tasks` are not registered.

- [ ] **Step 3: Register task routes with preflight admission checks**

The start route loads the account, auth, and saved preferences. Request `course_ids` replaces and persists the selection only when provided; otherwise use saved selection. Reject empty selections. When answering is enabled, resolve the encrypted connection and require `last_test_status == "success"` for the same base URL/model/key revision. Pass `answer_service.get_semaphore()` into the new `StudyRunContext`.

Return `201` with a public task snapshot. The answer key, password, cookies, notification tokens, and OCR keys must never be present in the snapshot.

The logs route returns:

```json
{
  "status": true,
  "data": {
    "items": [{"sequence": 1, "level": "info", "message": "course started", "timestamp": 1789371508.0}],
    "next_cursor": 1
  }
}
```

- [ ] **Step 4: Remove legacy Flask routes only after new tests pass**

Move all app creation to `webapp.create_app`, then delete the old module-level task dictionaries, global log queue, `LogCapture`, and legacy `/api/login`, `/api/courses`, `/api/config`, `/api/start`, `/api/task/<id>`, and `/api/logs/<id>` handlers from `app.py`. Replace the file with `app = create_app()` plus the local `app.run(host="127.0.0.1", port=5000, debug=False)` block.

- [ ] **Step 5: Run the complete backend suite**

Run: `python -m pytest tests -v`

Expected: all backend tests pass with no warnings about duplicate Loguru sinks or unclosed HTTP sessions.

- [ ] **Step 6: Commit the complete backend API**

```bash
git add app.py webapp/__init__.py webapp/routes/accounts.py webapp/routes/tasks.py tests/test_task_routes.py
git commit -m "feat: expose concurrent task api"
```

### Task 9: Establish the Frontend Router, API Layer, Tokens, and App Shell

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/vite.config.js`
- Modify: `web/tailwind.config.js`
- Modify: `web/src/index.css`
- Create: `web/src/test/setup.js`
- Create: `web/src/api/client.js`
- Create: `web/src/api/accounts.js`
- Create: `web/src/api/tasks.js`
- Create: `web/src/api/settings.js`
- Create: `web/src/hooks/usePolling.js`
- Create: `web/src/components/shell/AppShell.jsx`
- Create: `web/src/components/shell/Titlebar.jsx`
- Create: `web/src/components/shell/AccountSidebar.jsx`
- Create: `web/src/components/shell/AppShell.test.jsx`
- Create: `web/src/hooks/usePolling.test.jsx`
- Modify: `web/src/components/ui/Button.jsx`
- Modify: `web/src/components/ui/Input.jsx`
- Create: `web/src/components/ui/Alert.jsx`
- Create: `web/src/components/ui/Field.jsx`
- Create: `web/src/components/ui/Progress.jsx`
- Create: `web/src/components/ui/StatusDot.jsx`

**Interfaces:**
- Consumes: JSON APIs completed in Task 8
- Produces: `apiRequest(promise)`, `toApiError(error)`, and endpoint-specific API functions
- Produces: `usePolling(loader, {enabled, intervalMs, onData})`
- Produces: `AppShell({accounts, tasks, onAddAccount})` with an `<Outlet />`
- Produces: shared `Button`, `Input`, `Field`, `Alert`, `Progress`, and `StatusDot` props used by Tasks 10–12

- [ ] **Step 1: Install router, dialog, and frontend test dependencies**

Run:

```bash
cd web
npm install react-router-dom@^6.30.1 @radix-ui/react-dialog@^1.1.15
npm install --save-dev vitest@^2.1.9 jsdom@^25.0.1 @testing-library/react@^16.3.0 @testing-library/jest-dom@^6.6.3 @testing-library/user-event@^14.6.1
```

Add scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

- [ ] **Step 2: Write failing shell and polling tests**

```jsx
// web/src/components/shell/AppShell.test.jsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AppShell from './AppShell'

test('shows saved accounts and active task state', () => {
  render(
    <MemoryRouter>
      <AppShell
        accounts={[{ id: 'a', name: '张三', enabled: true }]}
        tasks={[{ id: 't', account_id: 'a', state: 'running' }]}
        onAddAccount={() => {}}
      />
    </MemoryRouter>,
  )
  expect(screen.getByText('张三')).toBeInTheDocument()
  expect(screen.getByLabelText('张三：运行中')).toBeInTheDocument()
})
```

```jsx
// web/src/hooks/usePolling.test.jsx
test('stops polling when enabled becomes false', async () => {
  vi.useFakeTimers()
  const loader = vi.fn().mockResolvedValue({ state: 'running' })
  const { rerender } = renderHook(({ enabled }) => usePolling(loader, { enabled, intervalMs: 2000 }), {
    initialProps: { enabled: true },
  })
  await vi.advanceTimersByTimeAsync(4000)
  rerender({ enabled: false })
  await vi.advanceTimersByTimeAsync(4000)
  expect(loader).toHaveBeenCalledTimes(3)
})
```

- [ ] **Step 3: Configure Vitest and run the tests to verify imports fail**

Add to `web/vite.config.js`:

```js
test: {
  environment: 'jsdom',
  setupFiles: './src/test/setup.js',
  css: true,
}
```

```js
// web/src/test/setup.js
import '@testing-library/jest-dom/vitest'
```

Run: `npm --prefix web test -- AppShell.test.jsx usePolling.test.jsx`

Expected: FAIL because `AppShell` and `usePolling` do not exist.

- [ ] **Step 4: Implement the API modules and terminal-aware polling**

`client.js` wraps Axios errors in an `ApiError` with `status`, `code`, and a user-facing `message`. Account/task/settings modules export one named function per endpoint; no component imports Axios directly.

```js
export function usePolling(loader, { enabled, intervalMs = 2000, onData } = {}) {
  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    let timer
    const poll = async () => {
      try {
        const data = await loader()
        if (active) onData?.(data)
      } finally {
        if (active) timer = window.setTimeout(poll, intervalMs)
      }
    }
    poll()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [enabled, intervalMs, loader, onData])
}
```

Callers set `enabled` to false for `completed`, `failed`, and `stopped` task states.

- [ ] **Step 5: Implement the macOS-style shell and design tokens**

Use a 220-pixel desktop sidebar, a 44-pixel titlebar, and flat content canvas. The titlebar/side navigation may use `backdrop-filter`; course/task content does not. Add a plain-color fallback under `@supports not (backdrop-filter: blur(1px))`.

Define semantic CSS variables for canvas, surface, sidebar material, separators, labels, blue accent, success, warning, and danger. Use the approved system font stack and tabular numerals for progress/time. Do not use `bg-gradient-*`, `shadow-xl`, pill containers for normal text, or looping animations.

`AppShell` renders account links with `aria-current`, task status labels, Add Account, Overview, and Settings. On widths below 768px, collapse the sidebar behind an accessible button and keep the main content in document order. Keep the existing `App.jsx` entry active until all replacement pages exist in Task 12, so this foundation commit does not strand the current Web build.

- [ ] **Step 6: Run frontend foundation tests and build**

Run: `npm --prefix web test -- AppShell.test.jsx usePolling.test.jsx`

Expected: all focused tests pass.

Run: `npm --prefix web run build`

Expected: Vite exits 0 and writes generated assets to `web/dist`.

- [ ] **Step 7: Commit the frontend foundation**

```bash
git add web/package.json web/package-lock.json web/vite.config.js web/tailwind.config.js web/src/index.css web/src/test web/src/api web/src/hooks web/src/components/shell web/src/components/ui
git commit -m "feat: add macos-style web application shell"
```

### Task 10: Build Account Management and the Multi-Account Overview

**Files:**
- Create: `web/src/components/accounts/AccountDialog.jsx`
- Create: `web/src/components/accounts/AccountDialog.test.jsx`
- Create: `web/src/pages/OverviewPage.jsx`
- Create: `web/src/pages/OverviewPage.test.jsx`
- Modify: `web/src/components/shell/AppShell.jsx`

**Interfaces:**
- Consumes: `listAccounts`, `createAccount`, `updateAccount`, `verifyAccount`, `setAccountEnabled`, `deleteAccount`, and `listTasks`
- Produces: `AccountDialog({open, account, onOpenChange, onSaved})`
- Produces: `OverviewPage` with account/task joining by `account_id`
- Produces: overview and account-dialog components ready for the route tree installed in Task 12

- [ ] **Step 1: Write failing account-dialog secret tests**

```jsx
// web/src/components/accounts/AccountDialog.test.jsx
test('editing an account never prefills its stored secret', async () => {
  render(<AccountDialog open account={{ id: 'a', name: '张三', username: '13800000000', has_secret: true }} onOpenChange={() => {}} onSaved={() => {}} />)
  expect(screen.getByLabelText('密码')).toHaveValue('')
  expect(screen.getByText('已保存密码；留空表示不修改')).toBeInTheDocument()
})


test('submits a new account and then verifies it', async () => {
  const user = userEvent.setup()
  createAccount.mockResolvedValue({ id: 'a', name: '张三' })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })
  render(<AccountDialog open onOpenChange={() => {}} onSaved={() => {}} />)
  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('密码'), 'local-secret')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))
  expect(await screen.findByText('账户验证成功')).toBeInTheDocument()
})
```

- [ ] **Step 2: Write failing overview state tests**

Add these exact overview cases:

```jsx
// web/src/pages/OverviewPage.test.jsx
test('empty state focuses the add-account action', async () => {
  listAccounts.mockResolvedValue([])
  listTasks.mockResolvedValue([])
  render(<OverviewPage />)
  expect(await screen.findByRole('button', { name: '添加第一个账户' })).toHaveFocus()
})


test.each([
  ['running', '运行中'],
  ['stopping', '正在停止'],
  ['failed', '失败'],
  ['completed', '已完成'],
])('renders %s account task state', async (state, label) => {
  listAccounts.mockResolvedValue([{ id: 'a', name: '张三', enabled: true }])
  listTasks.mockResolvedValue([{ id: 't', account_id: 'a', state, progress: 1, total: 2, error: state === 'failed' ? '连接失败' : null }])
  render(<OverviewPage />)
  expect(await screen.findByText(label)).toBeInTheDocument()
  expect(screen.getByText('张三')).toBeInTheDocument()
})


test('confirms delete and keeps active account when backend rejects it', async () => {
  const user = userEvent.setup()
  deleteAccount.mockRejectedValue(new ApiError('账户正在运行', 409, 'account_active'))
  render(<OverviewPage />)
  await user.click(await screen.findByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  await user.click(screen.getByRole('button', { name: '确认删除' }))
  expect(await screen.findByText('请先停止该账户的任务')).toBeInTheDocument()
  expect(screen.getByText('张三')).toBeInTheDocument()
})
```

Run: `npm --prefix web test -- AccountDialog.test.jsx OverviewPage.test.jsx`

Expected: FAIL because the account dialog and overview page do not exist.

- [ ] **Step 3: Implement the accessible account dialog**

Use Radix Dialog for focus trapping, Escape handling, title/description association, and return focus. The form fields are display name, username, authentication mode, password replacement, and optional cookie-header text. Never display an API-returned secret. Editing submits only non-empty replacement secrets.

Show validation errors inside an `aria-live="polite"` Alert and preserve entered non-secret values after failures. Disable only the submit button while saving; do not lock the whole dialog.

- [ ] **Step 4: Implement the all-account overview**

Join `accounts` and `tasks` by `task.account_id`. Render one semantic row per account with status, current course/chapter, static progress, concise error, and a context menu for Edit, Revalidate, Disable/Enable, and Delete. Deletion uses an explicit confirmation dialog and handles `account_active` without closing the row.

The initial route loads accounts and tasks in parallel. If no accounts exist, render the account-entry empty state rather than a blank shell.

- [ ] **Step 5: Run account UI tests and build**

Run: `npm --prefix web test -- AccountDialog.test.jsx OverviewPage.test.jsx AppShell.test.jsx`

Expected: all tests pass.

Run: `npm --prefix web run build`

Expected: build succeeds with no accessibility-related test warnings.

- [ ] **Step 6: Commit account management UI**

```bash
git add web/src/components/accounts web/src/components/shell/AppShell.jsx web/src/pages/OverviewPage.jsx web/src/pages/OverviewPage.test.jsx
git commit -m "feat: add multi-account overview ui"
```

### Task 11: Build the Course-First Launch Workbench and Settings

**Files:**
- Create: `web/src/pages/LaunchPage.jsx`
- Create: `web/src/pages/LaunchPage.test.jsx`
- Create: `web/src/pages/SettingsPage.jsx`
- Create: `web/src/pages/SettingsPage.test.jsx`

**Interfaces:**
- Consumes: account courses/preferences, task start, answer-connection, connection-test, and runtime-settings API functions
- Produces: Launch and Settings page components ready for the route tree installed in Task 12
- Produces launch payload `{course_ids: string[]}` after persisting per-account preferences
- Preserves: a blank API-key edit means keep the encrypted saved key

- [ ] **Step 1: Write failing launch-workbench tests**

```jsx
// web/src/pages/LaunchPage.test.jsx
test('keeps course choices scoped to the current account', async () => {
  listCourses.mockResolvedValue([
    { courseId: 'math', title: '高等数学' },
    { courseId: 'english', title: '大学英语' },
  ])
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], speed: 1.5, jobs: 4, notopen_action: 'retry' })
  renderPage('/accounts/account-a/launch')
  expect(await screen.findByRole('checkbox', { name: '高等数学' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: '大学英语' })).not.toBeChecked()
})


test('blocks start when answering is enabled but connection is untested', async () => {
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], answer_enabled: true })
  getAnswerConnection.mockResolvedValue({ enabled: true, last_test_status: 'untested' })
  renderPage('/accounts/account-a/launch')
  expect(await screen.findByText('请先在设置中测试答题连接')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始学习' })).toBeDisabled()
})
```

Add these exact workbench interactions:

```jsx
test('search select-all clear and stale refresh preserve the last course list', async () => {
  const user = userEvent.setup()
  listCourses.mockResolvedValueOnce(courseFixtures).mockRejectedValueOnce(new ApiError('网络错误', 503, 'courses_unavailable'))
  renderPage('/accounts/account-a/launch')
  await user.type(await screen.findByRole('searchbox', { name: '搜索课程' }), '英语')
  expect(screen.getByText('大学英语')).toBeInTheDocument()
  expect(screen.queryByText('高等数学')).not.toBeInTheDocument()
  await user.clear(screen.getByRole('searchbox', { name: '搜索课程' }))
  await user.click(screen.getByRole('button', { name: '全选' }))
  expect(screen.getAllByRole('checkbox', { checked: true })).toHaveLength(courseFixtures.length)
  await user.click(screen.getByRole('button', { name: '清空' }))
  await user.click(screen.getByRole('button', { name: '刷新课程' }))
  expect(await screen.findByText('正在显示上次成功加载的课程')).toBeInTheDocument()
  expect(screen.getByText('高等数学')).toBeInTheDocument()
})


test('saves preferences and navigates after successful start', async () => {
  const user = userEvent.setup()
  startTask.mockResolvedValue({ id: 'task-a' })
  renderPage('/accounts/account-a/launch')
  await user.click(await screen.findByRole('checkbox', { name: '高等数学' }))
  await user.click(screen.getByRole('button', { name: '开始学习' }))
  expect(savePreferences).toHaveBeenCalledBefore(startTask)
  expect(currentLocation()).toBe('/tasks/task-a')
})


test.each([
  [new ApiError('账户已有任务', 409, 'account_active'), '该账户已有任务在运行'],
  [new ApiError('账户已停用', 409, 'account_disabled'), '请先启用该账户'],
])('shows start conflict without losing selection', async (error, message) => {
  startTask.mockRejectedValue(error)
  renderPage('/accounts/account-a/launch')
  await userEvent.click(await screen.findByRole('button', { name: '开始学习' }))
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: '高等数学' })).toBeChecked()
})
```

- [ ] **Step 2: Write failing settings and API-key tests**

```jsx
// web/src/pages/SettingsPage.test.jsx
test('shows a mask and does not render the saved api key', async () => {
  getAnswerConnection.mockResolvedValue({
    enabled: true,
    base_url: 'http://localhost:8849/v1',
    model: 'gemini-3.8-flash-high',
    has_api_key: true,
    api_key_mask: 'sk-••••••',
  })
  renderPage('/settings')
  expect(await screen.findByText('sk-••••••')).toBeInTheDocument()
  expect(screen.getByLabelText('替换 API Key')).toHaveValue('')
})
```

Add these exact Settings cases:

```jsx
test('tests the edited connection and preserves a saved key when replacement is blank', async () => {
  const user = userEvent.setup()
  testAnswerConnection.mockResolvedValue({ ok: true, model_found: true })
  renderPage('/settings')
  await user.clear(await screen.findByLabelText('基础地址'))
  await user.type(screen.getByLabelText('基础地址'), 'http://localhost:8849/v1')
  await user.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接成功，模型可用')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '保存连接' }))
  expect(saveAnswerConnection.mock.calls[0][0]).not.toHaveProperty('api_key')
})


test('requires confirmation before clearing the saved key', async () => {
  const user = userEvent.setup()
  renderPage('/settings')
  await user.click(await screen.findByRole('button', { name: '清除 API Key' }))
  expect(clearAnswerKey).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '确认清除' }))
  expect(clearAnswerKey).toHaveBeenCalledTimes(1)
})


test.each([
  ['最大同时运行账户数', '0', '请输入 1 到 10'],
  ['全局答题并发数', '0', '请输入大于 0 的整数'],
  ['请求超时', '-1', '请输入大于 0 的秒数'],
])('validates %s', async (label, value, message) => {
  renderPage('/settings')
  await userEvent.clear(await screen.findByLabelText(label))
  await userEvent.type(screen.getByLabelText(label), value)
  await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
  expect(await screen.findByText(message)).toBeInTheDocument()
})


test('does not print a submitted api key when connection testing fails', async () => {
  const user = userEvent.setup()
  const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  testAnswerConnection.mockRejectedValue(new ApiError('连接失败', 503, 'answer_unavailable'))
  renderPage('/settings')
  await user.type(await screen.findByLabelText('替换 API Key'), 'local-test-secret')
  await user.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接失败')).toBeInTheDocument()
  expect(JSON.stringify(consoleSpy.mock.calls)).not.toContain('local-test-secret')
  consoleSpy.mockRestore()
})
```

Run: `npm --prefix web test -- LaunchPage.test.jsx SettingsPage.test.jsx`

Expected: FAIL because both pages do not exist.

- [ ] **Step 3: Implement the launch workbench**

Use the approved two-column desktop layout: searchable checkbox course list on the left and a sticky inspector on the right. Advanced settings link to Settings. Use normal rectangular controls and one blue primary button. Place the inspector after the list below 768px.

On start, save preferences first, then call `startTask(accountId, {course_ids})`, and navigate to `/tasks/<task-id>`. Preserve the last course list when refresh fails and label it stale.

- [ ] **Step 4: Implement shared connection and runtime settings**

Display the user-visible base URL exactly as saved. The API-key field is always empty on load; show a separate mask/status. Test Connection sends the current draft, including a new key only when entered. On successful save, clear the local key field immediately.

Add sections for Answer Connection, Runtime Limits, account-specific Notification, and account-specific OCR. Keep only Answer Connection and Runtime Limits open initially; other sections use normal disclosure controls, not nested card stacks.

- [ ] **Step 5: Run workbench/settings tests and build**

Run: `npm --prefix web test -- LaunchPage.test.jsx SettingsPage.test.jsx`

Expected: all tests pass.

Run: `npm --prefix web run build`

Expected: build succeeds; generated `web/dist` remains ignored.

- [ ] **Step 6: Commit launch and settings UI**

```bash
git add web/src/pages/LaunchPage.jsx web/src/pages/LaunchPage.test.jsx web/src/pages/SettingsPage.jsx web/src/pages/SettingsPage.test.jsx
git commit -m "feat: add course launch and answer settings ui"
```

### Task 12: Build the Per-Account Task Monitor

**Files:**
- Create: `web/src/components/tasks/TaskStatus.jsx`
- Create: `web/src/components/tasks/TaskProgress.jsx`
- Create: `web/src/components/tasks/TaskLog.jsx`
- Create: `web/src/pages/TaskPage.jsx`
- Create: `web/src/pages/TaskPage.test.jsx`
- Modify: `web/src/App.jsx`
- Remove: `web/src/api/axios.js`
- Remove: `web/src/components/Login.jsx`
- Remove: `web/src/components/CourseSelection.jsx`
- Remove: `web/src/components/AdvancedSettings.jsx`
- Remove: `web/src/components/StudyProgress.jsx`
- Remove: `web/src/components/ui/Card.jsx`
- Remove: `web/src/components/ui/Label.jsx`

**Interfaces:**
- Consumes: `getTask`, `getTaskDetails`, `getTaskLogs`, and `cancelTask`
- Consumes: `usePolling` from Task 9
- Produces: the complete route tree for `/`, `/accounts/new`, `/accounts/:accountId/launch`, `/tasks/:taskId`, and `/settings`
- Produces cursor-preserving log state `{items, nextCursor}`

- [ ] **Step 1: Write failing monitor and polling tests**

```jsx
// web/src/pages/TaskPage.test.jsx
test('renders only logs returned for the selected task', async () => {
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state: 'running', progress: 1, total: 2 })
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  getTaskLogs.mockResolvedValue({
    items: [{ sequence: 1, level: 'info', message: 'account-a-only', timestamp: 1789371508 }],
    next_cursor: 1,
  })
  renderPage('/tasks/task-a')
  expect(await screen.findByText('account-a-only')).toBeInTheDocument()
  expect(screen.queryByText('account-b-only')).not.toBeInTheDocument()
})


test('stop changes to a noninteractive stopping state', async () => {
  const user = userEvent.setup()
  cancelTask.mockResolvedValue({ id: 'task-a', state: 'stopping' })
  renderPage('/tasks/task-a')
  await user.click(await screen.findByRole('button', { name: '停止任务' }))
  expect(screen.getByRole('button', { name: '正在停止' })).toBeDisabled()
})
```

Add these exact terminal, detail, reconnect, and cursor cases:

```jsx
test.each([
  ['completed', '已完成'],
  ['failed', '失败'],
  ['stopped', '已停止'],
])('stops polling for terminal state %s', async (state, label) => {
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state })
  renderPage('/tasks/task-a')
  expect(await screen.findByText(label)).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(6000)
  expect(getTask).toHaveBeenCalledTimes(1)
})


test('retains snapshot while reconnecting and recovers on the next poll', async () => {
  getTask
    .mockResolvedValueOnce(runningSnapshot)
    .mockRejectedValueOnce(new ApiError('offline', 503, 'network_error'))
    .mockResolvedValueOnce({ ...runningSnapshot, progress: 2 })
  renderPage('/tasks/task-a')
  expect(await screen.findByText('1 / 3')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(2000)
  expect(screen.getByText('正在重新连接')).toBeInTheDocument()
  expect(screen.getByText('1 / 3')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(2000)
  expect(screen.queryByText('正在重新连接')).not.toBeInTheDocument()
  expect(screen.getByText('2 / 3')).toBeInTheDocument()
})


test('appends unique log sequences and expands course details', async () => {
  getTaskLogs
    .mockResolvedValueOnce({ items: [{ sequence: 1, level: 'info', message: 'first', timestamp: 1 }], next_cursor: 1 })
    .mockResolvedValueOnce({ items: [{ sequence: 2, level: 'info', message: 'second', timestamp: 2 }], next_cursor: 2 })
  getTaskDetails.mockResolvedValue({
    courses: [{ id: 'math', title: '高等数学', chapters: [{ id: 'one', title: '第一章', status: 'completed' }] }],
    active_jobs: { video: { job_name: '教学视频', progress: 50, current_time: 30, duration: 60 } },
  })
  renderPage('/tasks/task-a')
  await userEvent.click(await screen.findByRole('button', { name: '展开高等数学' }))
  expect(screen.getByText('第一章')).toBeInTheDocument()
  expect(screen.getByRole('progressbar', { name: '教学视频' })).toHaveAttribute('aria-valuenow', '50')
  await vi.advanceTimersByTimeAsync(2000)
  expect(screen.getAllByText('first')).toHaveLength(1)
  expect(screen.getByText('second')).toBeInTheDocument()
})


test('unknown task links back to overview and launch', async () => {
  getTask.mockRejectedValue(new ApiError('不存在', 404, 'task_not_found'))
  renderPage('/tasks/missing')
  expect(await screen.findByRole('link', { name: '返回任务总览' })).toHaveAttribute('href', '/')
})
```

- [ ] **Step 2: Run monitor tests and verify components are missing**

Run: `npm --prefix web test -- TaskPage.test.jsx`

Expected: FAIL because `TaskPage` and task components do not exist.

- [ ] **Step 3: Implement semantic task status and static progress components**

`TaskStatus` maps backend states to visible Chinese labels and semantic colors. `TaskProgress` clamps to `0..100`, uses `role="progressbar"`, `aria-valuemin`, `aria-valuemax`, and `aria-valuenow`, and applies no animated width transition. Time/count values use tabular numerals.

`TaskLog` renders a selectable monospace list with timestamp and level text in addition to color. Keep scroll position unless the user is already near the bottom; do not force-scroll on every poll.

- [ ] **Step 4: Implement coordinated snapshot/details/log polling**

Poll snapshot and details every two seconds while `running` or `stopping`. Fetch logs with the last `next_cursor`, append unique sequences, and stop all timers in terminal states. A transient poll failure retains the last snapshot and shows `正在重新连接`; successful polling clears it.

Cancellation opens a confirmation dialog naming the account/course, calls the cancel endpoint once, and renders `正在停止` until the backend reaches `stopped`.

Replace `App.jsx` with `BrowserRouter` routes under `AppShell`, including a not-found view that links back to Overview. Remove the old login/course/settings/progress components and the old Axios module only after the new route tests pass.

- [ ] **Step 5: Run the complete frontend suite and production build**

Run: `npm --prefix web test`

Expected: all frontend tests pass.

Run: `npm --prefix web run build`

Expected: build succeeds and contains no unresolved chunk or accessibility warnings.

- [ ] **Step 6: Commit the task monitor**

```bash
git add web/src/App.jsx web/src/api/axios.js web/src/components/Login.jsx web/src/components/CourseSelection.jsx web/src/components/AdvancedSettings.jsx web/src/components/StudyProgress.jsx web/src/components/ui/Card.jsx web/src/components/ui/Label.jsx web/src/components/tasks web/src/pages/TaskPage.jsx web/src/pages/TaskPage.test.jsx
git commit -m "feat: add isolated task monitoring ui"
```

### Task 13: Package, Configure, and Verify the Docker Deployment

**Files:**
- Modify: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `requirements.txt`
- Modify: `README.md`
- Create: `tests/test_docker_contract.py`

**Interfaces:**
- Consumes: backend and frontend artifacts from Tasks 1–12
- Produces: image command `gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5000 app:app`
- Produces: Compose Web URL `http://127.0.0.1:5001`
- Produces: named volume mounted at `/app/data`
- Produces: `host.docker.internal:host-gateway` route to the host answer service on port `8849`

- [ ] **Step 1: Write failing static Docker-contract tests**

```python
# tests/test_docker_contract.py
from pathlib import Path
import yaml


def test_compose_binds_only_localhost_and_persists_data():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    web = compose["services"]["web"]
    assert web["ports"] == ["127.0.0.1:5001:5000"]
    assert "chaoxing-data:/app/data" in web["volumes"]
    assert "host.docker.internal:host-gateway" in web["extra_hosts"]


def test_dockerfile_builds_frontend_and_runs_one_gunicorn_worker():
    dockerfile = Path("Dockerfile").read_text()
    assert "npm run build" in dockerfile
    assert "COPY --from=web-builder" in dockerfile
    assert 'EXPOSE 5000' in dockerfile
    assert '"--workers", "1"' in dockerfile
```

Add `PyYAML>=6.0.2,<7` to `requirements-dev.txt` for this test only.

- [ ] **Step 2: Run contract tests and verify Compose is missing**

Run: `python -m pytest tests/test_docker_contract.py -v`

Expected: FAIL because `compose.yaml` and the multi-stage Docker contract do not exist.

- [ ] **Step 3: Replace the Dockerfile with a multi-stage Web build**

Use Node 20 for deterministic frontend installation and Python 3.13 slim for runtime:

```dockerfile
FROM node:20-bookworm-slim AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    CHAOXING_DATA_DIR=/app/data \
    CHAOXING_RUNNING_IN_DOCKER=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY --from=web-builder /build/web/dist ./web/dist
RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 5000
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "app:app"]
```

Add `gunicorn>=23,<24` to `requirements.txt`. `.dockerignore` must exclude `.git`, `.env*` except `.env.example`, `.superpowers`, `data`, `web/dist`, `web/node_modules`, Python caches, logs, cookies, `config.ini`, and `web_config.json`.

- [ ] **Step 4: Add Compose, health check, and non-secret environment example**

```yaml
# compose.yaml
services:
  web:
    build: .
    image: chaoxing-fanya:web-local
    ports:
      - "127.0.0.1:5001:5000"
    volumes:
      - chaoxing-data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 20s

volumes:
  chaoxing-data:
```

`.env.example` contains only documented non-secret overrides such as `CHAOXING_MAX_ACTIVE_ACCOUNTS=3`; it must not contain a sample that resembles the user's real key.

```dotenv
CHAOXING_MAX_ACTIVE_ACCOUNTS=3
```

Add `data/` and `.superpowers/` to `.gitignore`. Keep `!web/src/lib/` and `!web/src/lib/utils.js` so the existing shared helper remains tracked.

- [ ] **Step 5: Update README with exact deployment and port guidance**

Document:

```text
Web UI:            http://127.0.0.1:5001
Container Web:     0.0.0.0:5000
Host answer API:   http://localhost:8849/v1
Container target:  http://host.docker.internal:8849/v1
Persistent data:   named volume chaoxing-data at /app/data
```

Explain that the API key is entered once in Settings, encrypted in the local volume, and not placed in `.env`, source, or image layers. Include `docker compose up --build -d`, `docker compose ps`, health-check, logs, stop, and volume-backup commands.

Use these exact command examples:

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:5001/api/health
docker compose logs -f web
docker compose stop web
docker run --rm -v chaoxing-fanya_chaoxing-data:/source -v "$PWD":/backup alpine tar -czf /backup/chaoxing-data-backup.tgz -C /source .
```

- [ ] **Step 6: Run all static tests and clean builds**

Run: `python -m pytest tests -v`

Expected: all backend and Docker-contract tests pass.

Run: `npm --prefix web test`

Expected: all frontend tests pass.

Run: `npm --prefix web run build`

Expected: frontend production build succeeds from the lockfile state.

- [ ] **Step 7: Build and start the Docker deployment**

Run: `docker compose build --no-cache web`

Expected: both build stages complete and image `chaoxing-fanya:web-local` is created.

Run: `docker compose up -d web`

Expected: the Web container starts and transitions to healthy.

Run: `docker compose ps`

Expected: `127.0.0.1:5001->5000/tcp` is the only published application port and health is `healthy`.

- [ ] **Step 8: Configure the provided local answer credentials through the running app**

Open `http://127.0.0.1:5001/settings`, enter `http://localhost:8849/v1`, `gemini-3.8-flash-high`, timeout `30`, retry count `3`, global concurrency `3`, and paste the API key already provided by the user into the password-style Replace API Key field. Save it through the running Flask API so the value enters the encrypted local store; do not paste it into this plan, a shell history entry, Git-tracked file, Docker build argument, or image layer.

Perform `POST /api/settings/answer-connection/test` against the saved connection. Expected: `status=true`, reachable service, and `model_found=true`. Then inspect the Settings response and SQLite bytes to confirm the full key is absent from both.

- [ ] **Step 9: Run live port, persistence, and host-gateway smoke checks**

Run: `curl -fsS http://127.0.0.1:5001/api/health`

Expected: `{"status":true,"data":{"service":"chaoxing-web"}}`.

Restart the Web container and confirm accounts, preferences, and answer-connection status remain present while no task is automatically resumed. Start two mocked or safe test accounts in integration mode and confirm both task rows update without crossed logs.

- [ ] **Step 10: Commit deployment support and documentation**

```bash
git add Dockerfile compose.yaml .dockerignore .env.example .gitignore requirements.txt requirements-dev.txt README.md tests/test_docker_contract.py
git commit -m "feat: add local multi-account web deployment"
```

## Final Verification Gate

- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run `python -m pytest tests -v` and record the passing count.
- [ ] Run `npm --prefix web test` and record the passing count.
- [ ] Run `npm --prefix web run build` and record the successful Vite output.
- [ ] Run `docker compose config` and confirm the rendered bind remains `127.0.0.1:5001:5000`.
- [ ] Run `docker compose ps` and confirm one healthy Web container and no unexpected published ports.
- [ ] Open `http://127.0.0.1:5001` and manually verify Add Account, Task Overview, Launch, Task Monitor, Settings, keyboard focus, narrow-screen collapse, and reduced-motion behavior.
- [ ] Test the saved local answer connection using the provided runtime key and confirm the key is absent from API responses, logs, Git diff, and image history.
- [ ] Start two accounts concurrently, stop one, and confirm the other continues with its own session, state, details, progress, and logs.
- [ ] Run `python main.py --help` and one mocked CLI regression to confirm the non-Web entry point still works.
