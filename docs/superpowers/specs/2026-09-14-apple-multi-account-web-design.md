# Apple-Style Multi-Account Web Redesign

**Status:** Approved on 2026-09-14

## Objective

Rebuild the Web interface as a desktop-first, macOS-style local application that can save multiple Chaoxing accounts, run one learning task per account concurrently, monitor every active account without cross-contamination, and use one shared OpenAI-compatible service for answering questions.

The redesign replaces the existing three-step gradient/card interface. It does not copy the current fork's visual identity or require the old `web/dist` directory to be committed.

## Approved Direction

- Product structure: A1, a course-first launch workbench.
- Device strategy: D1, desktop first.
- Visual language: a macOS-native utility, not an Apple marketing page.
- Navigation and controls may use restrained translucent material; content remains on flat, legible surfaces.
- Use the system font stack (`-apple-system`, `BlinkMacSystemFont`, `"SF Pro Text"`, `"PingFang SC"`, sans-serif). Do not bundle Apple's proprietary fonts or assets.
- Use blue as the single interactive accent. Do not use gradients, purple accents, glows, decorative animation, oversized marketing typography, or excessive floating cards.
- Motion is limited to necessary interaction feedback of 200 ms or less and must respect `prefers-reduced-motion`.

## Scope

### Included

- Persistent local account profiles with encrypted credentials.
- Account validation, editing, disabling, and deletion.
- Independent HTTP sessions and cookies for every account.
- One active task per account and multiple active accounts at the same time.
- A unified task overview plus account-specific launch and task-monitor pages.
- Cooperative task cancellation.
- Per-task state, details, and bounded log buffers.
- One shared OpenAI-compatible answer connection for all accounts.
- Per-account answering policy, course selection, speed, chapter concurrency, and unopened-chapter behavior.
- Connection testing, URL normalization, secret masking, retry behavior, and shared answer-request concurrency.
- A production Web container that builds the frontend inside the image.
- Web host binding `127.0.0.1:5001`, container port `5000`, persistent `/app/data`, and access to a host service on port `8849`.
- Automated backend, frontend, integration, and Docker smoke tests.

### Not Included

- Cloud sync, remote multi-user access, or exposing the Web UI to the LAN by default.
- Resuming an interrupted learning task after the container or process restarts.
- Multiple simultaneous tasks for the same Chaoxing account.
- A durable task-history product or analytics dashboard.
- Copying Apple logos, icons, proprietary fonts, or pixel-identical Apple screens.
- Embedding credentials or API keys in source code, Docker image layers, frontend bundles, fixtures, screenshots, or Git history.

## Core Concepts

### Account Profile

An account profile is a long-lived local record. It has a stable UUID, display name, Chaoxing username, authentication mode, encrypted secret, encrypted cookie data when available, enabled state, last verification result, course selection, and per-account learning preferences.

Disabling an account prevents new tasks but does not delete its data. Deleting an account requires explicit confirmation and is rejected while the account has an active task.

### Activity Task

An activity task is one run created when a user starts learning for an account. At any moment an account maps to zero or one active task. Different accounts may each have an active task concurrently.

Task states are:

- `running`
- `stopping`
- `completed`
- `failed`
- `stopped`

A completed, failed, or stopped task remains visible for the current server process until the account starts another task. Active tasks do not resume after a process restart; all accounts return to the idle state.

### Shared Answer Connection

All accounts use one server-side OpenAI-compatible connection. The approved initial values are:

- Base URL: `http://localhost:8849/v1`
- Model: `gemini-3.8-flash-high`
- API key: supplied at runtime through the settings UI and never recorded in this document

The browser never calls the answer service directly. Flask performs connection tests and completion requests so the API key remains server-side and CORS is irrelevant.

The base URL is a service root, not necessarily a full completion URL. The backend normalizes it as follows:

- `.../v1` becomes `.../v1/chat/completions` for answer requests.
- `.../v1/` becomes `.../v1/chat/completions`.
- A URL already ending in `/chat/completions` is preserved.
- Any query string, fragment, embedded credentials, or non-HTTP(S) scheme is rejected.

When the Web app runs in Docker, a loopback hostname in the saved URL is translated only for outbound container requests:

- `localhost` or `127.0.0.1` becomes `host.docker.internal`.
- The saved and displayed value remains the user's original local URL.
- Linux Compose adds `host.docker.internal:host-gateway`.

## User Experience

### 1. Account Entry

If no accounts exist, the app opens an account-entry view. The user provides a display name, username, password, and optional cookie-based authentication data. Submitting validates the credentials before saving the profile.

When accounts already exist, the app opens directly to Task Overview. The account sidebar provides Add Account, Edit, Disable/Enable, Revalidate, and Delete actions. Secrets are never prefilled or returned; an empty secret field during editing means keep the stored value.

### 2. Task Overview

Task Overview is the default signed-in view. Each account row shows:

- Display name and masked username.
- Enabled or disabled state.
- Idle, running, stopping, completed, failed, or stopped task state.
- Current course and chapter when running.
- Progress and a concise last error when relevant.
- Primary action: Configure, View Task, or Review Error.

This page aggregates only public task state. It does not combine credentials, cookies, answer payloads, or raw logs across accounts.

### 3. Course-First Launch Workbench

Selecting an account opens its launch workbench:

- Main area: searchable course list with Select All, Clear, selected count, and refresh.
- Right inspector: playback speed, chapter concurrency, unopened-chapter behavior, per-account answer enablement, coverage threshold, and auto-submit policy.
- Advanced notification and OCR fields live on Settings, not in the launch inspector.
- The page remembers course choices and preferences for that account.
- Starting is blocked when the account is disabled, already has an active task, has no selected courses, has invalid preferences, or has answering enabled while the shared answer connection has not passed a connection check.

Starting successfully navigates to the account's Task Monitor.

### 4. Task Monitor

The monitor shows:

- Task state and elapsed time.
- Overall course, chapter, and task counts.
- Current course and chapter.
- Active video jobs with current time, duration, and static progress bars.
- Expandable course and chapter details.
- A task-specific log stream with level and timestamp.
- Stop Task while running, then a non-interactive Stopping state.
- Return to Launch after completion, failure, or cancellation.

Polling uses a two-second interval while running or stopping and stops for terminal states. Log polling is cursor-based and non-destructive; viewing one task never removes another task's logs.

### 5. Connections and Settings

Settings contains:

- Shared answer connection: enabled, base URL, model, API key replacement, request timeout, retry count, and maximum global concurrency.
- Test Connection, which verifies authentication and that the configured model can be reached.
- Per-account notification and OCR configuration.
- Global maximum number of concurrently active accounts, default `3`, configurable from `1` to `10`.

The answer API key is rendered as `Configured` plus a short mask, never as the stored plaintext. Saving other fields without a new key preserves the existing key. Clearing the key requires a distinct Clear Key action with confirmation.

## Backend Architecture

### Application Factory

`app.py` becomes a thin entry point that exposes `app = create_app()`. A new `webapp` package owns storage, services, routes, error mapping, and static-file fallback. Tests create isolated apps with temporary data directories and injected fake study runners.

### Per-Account HTTP Session

The current singleton `SessionManager` is incompatible with parallel accounts because all `Chaoxing` instances share one cookie jar. Replace it with an instance-owned `requests.Session` on `Chaoxing`.

`Chaoxing` accepts initial cookies and an optional cookie-update callback. Every method uses `self.session`. Live-task code receives that same session explicitly. CLI mode keeps file-based cookie loading/saving through compatibility helpers, but Web mode persists each account's cookie dictionary in its encrypted account record.

No account may read or overwrite another account's cookies.

### Persistent Store

Use Python's built-in `sqlite3` with a database at `/app/data/chaoxing-web.sqlite3` in Docker and `./data/chaoxing-web.sqlite3` by default outside Docker.

Tables:

- `accounts`: identity, display name, username, encrypted credential, encrypted cookies, enabled flag, verification fields, timestamps.
- `account_preferences`: selected course IDs and JSON preference payload keyed by account ID.
- `settings`: typed JSON values for global limits and non-secret connection fields.
- `secrets`: encrypted values for the answer API key and future server-side secrets.

Use `cryptography.fernet.Fernet` authenticated encryption. The master key is generated once at `<data-dir>/secret.key`, written with owner-only permissions, and mounted in the same persistent data volume. Losing this key makes encrypted values unrecoverable. The API never logs decrypted values.

SQLite connections are short-lived per operation, use WAL mode, enforce foreign keys, and serialize schema initialization. Store methods return typed dataclasses rather than database rows.

### Task Manager

A singleton `TaskManager` exists per Flask process and is injected into task routes. It owns:

- A lock-protected mapping from task ID to `TaskRuntime`.
- A lock-protected mapping from account ID to active task ID.
- A configurable global active-account semaphore.
- One cancellation `threading.Event` per task.
- One bounded log buffer per task, capped at 5,000 entries.
- A monotonically increasing log sequence per task.

Starting returns HTTP `409` if the same account already has an active task or the global active-account limit is reached. Terminal cleanup releases the account and global slots but retains the terminal snapshot for the current process.

Cancellation is cooperative. The runner checks the event before each course, chapter, and job and after blocking requests. It does not kill Python threads. Cancellation transitions `running → stopping → stopped`.

### Log Isolation

The current global queue is removed from Web task delivery because `GET /api/logs/<task_id>` drains and discards entries belonging to other tasks. Register one process-wide Loguru sink that routes records by `record["extra"]["task_id"]` into the matching task buffer.

Every task thread runs inside `logger.contextualize(task_id=...)`. Worker, retry, live, and thread-pool job entry points re-establish the same context because new threads do not safely inherit it. Logs without a task ID continue to the normal CLI/file sinks but never appear in a task buffer.

### Answer Concurrency

The shared answer settings are loaded once per task start. Every account gets its own AI answer-provider instance, but all provider instances receive the same process-wide bounded semaphore. This enforces the configured global completion concurrency across all accounts.

Per-account policies control whether answers are enabled, coverage threshold, and auto-submit. A shared connection failure does not merge or cancel account tasks. Runtime retries use the configured timeout and retry count. If no answer is available after retries, the question remains unanswered; submission remains subject to the existing coverage threshold and must not proceed below it.

## HTTP API

All JSON responses retain the project's envelope:

```json
{"status": true, "data": {}}
```

Errors use:

```json
{"status": false, "msg": "Human-readable message", "code": "stable_machine_code"}
```

Endpoints:

- `GET /api/accounts`
- `POST /api/accounts`
- `PATCH /api/accounts/<account_id>`
- `DELETE /api/accounts/<account_id>`
- `POST /api/accounts/<account_id>/verify`
- `GET /api/accounts/<account_id>/courses?refresh=0|1`
- `GET /api/accounts/<account_id>/preferences`
- `PUT /api/accounts/<account_id>/preferences`
- `GET /api/tasks`
- `POST /api/accounts/<account_id>/tasks`
- `GET /api/tasks/<task_id>`
- `GET /api/tasks/<task_id>/details`
- `GET /api/tasks/<task_id>/logs?after=<sequence>`
- `POST /api/tasks/<task_id>/cancel`
- `GET /api/settings/answer-connection`
- `PUT /api/settings/answer-connection`
- `POST /api/settings/answer-connection/test`
- `GET /api/settings/runtime`
- `PUT /api/settings/runtime`
- `GET /api/health`

Account responses return `has_secret` and `has_cookies` booleans, never credential values. Answer-connection responses return `has_api_key`, never the API key.

The legacy `/api/login`, `/api/courses`, `/api/config`, `/api/start`, `/api/task/<id>`, and `/api/logs/<id>` routes are removed after the new frontend and new endpoint tests are in place. CLI behavior through `main.py` remains supported.

## Frontend Architecture

Use React 18, Vite, Tailwind CSS, `react-router-dom`, Lucide icons, and accessible primitives. Add Vitest and Testing Library. Do not add a global state library; server state is small enough for focused hooks and route-level state.

Routes:

- `/` — Task Overview
- `/accounts/new` — Add Account
- `/accounts/:accountId/launch` — Course-First Launch Workbench
- `/tasks/:taskId` — Task Monitor
- `/settings` — Connections and Settings

The desktop shell contains a macOS-style titlebar, an account-aware sidebar, and a flat content region. On narrow screens the sidebar collapses and the right inspector follows the main course list in document order. Use `min-h-dvh`, not `h-screen`.

The UI must provide visible labels, keyboard focus, native or accessible dialog behavior, 44-pixel minimum touch targets on narrow layouts, `aria-live` for asynchronous status, text alternatives for icon-only controls, static progress transitions, and a reduced-motion mode.

## Error Behavior

- Account validation errors stay in the account form and do not erase entered non-secret fields.
- Starting an already-active account returns and displays a conflict without creating a second thread.
- A failure in one account changes only that task to `failed`.
- Answer connection failure is displayed in Settings and blocks tasks whose answer policy is enabled; users may disable answering and start again.
- Course refresh failure leaves the last successful course list visible with a stale-data notice.
- Task polling failure shows a reconnecting status and preserves the last snapshot.
- Unknown task or account IDs render a clear empty/error page with navigation back to Overview.
- Sensitive values, full cookies, raw credentials, and authorization headers are excluded from exceptions and logs.

## Docker Deployment

Use a multi-stage Dockerfile:

1. Node builder installs `web/package-lock.json` dependencies and runs `npm run build`.
2. Python runtime installs backend dependencies, copies backend source, and copies the generated `web/dist` from the builder.
3. The runtime starts Gunicorn with one worker and multiple threads because task state is process-local.

The image exposes container port `5000`. `compose.yaml` binds only:

```yaml
ports:
  - "127.0.0.1:5001:5000"
```

It mounts a named volume at `/app/data`, configures `host.docker.internal:host-gateway`, and health-checks `/api/health`. The old CLI remains available by overriding the container command, but the default image command starts the Web application.

`web/dist` remains ignored and uncommitted because the Docker build generates it deterministically.

## Verification and Acceptance Criteria

- Two fake accounts can run concurrently without sharing sessions, cookies, course selections, task state, details, or logs.
- A second task for the same account receives HTTP `409`.
- Cancelling one task does not alter another active task.
- Concurrent task logs remain complete, ordered by per-task sequence, and never appear under the wrong task.
- Stored credentials and API keys are not readable as plaintext in SQLite or API responses.
- The supplied OpenAI-compatible base URL is normalized correctly, and connection testing validates the configured model without exposing the key.
- In Docker, the Web UI responds at `http://127.0.0.1:5001`, the health endpoint passes, data survives a container restart, and the container can reach the host answer service on port `8849`.
- The frontend builds from a clean checkout without a committed `web/dist` directory.
- Keyboard navigation, focus indication, responsive collapse, empty states, loading states, and reduced-motion behavior are covered by frontend tests and manual smoke checks.
- Existing CLI startup and one-account learning flow still pass regression tests after the session refactor.
