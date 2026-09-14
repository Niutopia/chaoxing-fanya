# Task 12 — Per-account task monitor

## Verification evidence

- TDD RED: before implementation, `npm --prefix web test -- TaskPage.test.jsx` failed during module resolution because `src/pages/TaskPage.jsx` did not exist (`Failed to resolve import "./TaskPage"`).
- Focused GREEN: `npm --prefix web test -- TaskPage.test.jsx OverviewPage.test.jsx LaunchPage.test.jsx SettingsPage.test.jsx AppShell.test.jsx` passed — 5 test files, 43 tests.
- Final frontend suite: `npm --prefix web test` passed — 9 test files, 63 tests.
- Production build: `npm --prefix web run build` passed — Vite transformed 1,382 modules and emitted `dist/index.html`, CSS, and JS assets with no unresolved imports or accessibility diagnostics. The command only reported the existing stale Baseline/Browserslist data notices.
- `git diff --check` passed with no whitespace errors.
- A source scan after legacy removal found no imports of `api/axios`, `Login`, `CourseSelection`, `AdvancedSettings`, `StudyProgress`, `ui/Card`, or `ui/Label`.

## Functional evidence

- `App.jsx` now owns one account/task snapshot, wraps the complete BrowserRouter route tree in `AppShell`, and wires `/`, `/accounts/new`, `/accounts/:accountId/launch`, `/tasks/:taskId`, `/settings`, plus an unknown-route view.
- The empty root waits for the initial account request, then redirects to `/accounts/new`; the account dialog keeps a newly persisted profile mounted for verification/retry and navigates to that account’s launch page only after verification succeeds.
- `TaskPage` calls `getTask`, `getTaskDetails`, and `getTaskLogs` through `usePolling` at two-second intervals while a task is active. It preserves the last snapshot during transient errors, displays reconnecting/error/empty states, appends unique sequence-numbered logs from the latest cursor, and resets all cursor/seen state when the task id changes.
- Terminal snapshots (`completed`, `failed`, `stopped`) disable both polling loops. `usePolling` cleanup cancels timers and ignores late promises on unmount or route change, so the shell does not create a second monitor loop.
- Cancellation requires confirmation, names the account/course, calls `cancelTask` once, and holds a noninteractive `正在停止` state until the backend snapshot changes.
- Account/task payloads are reduced to public fields, task errors/log messages redact common credential assignments and bearer values, and no durable browser history is written.
- `TaskStatus`, `TaskProgress`, and `TaskLog` provide semantic status text, clamped accessible progress meters, selectable timestamped monospace logs, preserved scroll position, 44px touch targets/focus rings, and no progress-width transition.

## Commit

The implementation is committed as:

`feat: add isolated task monitoring ui`
