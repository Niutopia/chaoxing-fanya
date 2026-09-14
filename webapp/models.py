"""Typed values used by the web application's persistence layer.

The public models intentionally expose only metadata about secrets.  The
store returns the credential-bearing models only from the explicit resolver
methods that need them.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AccountProfile:
    """Non-sensitive information used to render an account in the UI."""

    id: str
    name: str
    username: str
    enabled: bool
    has_secret: bool
    has_cookies: bool
    verification_status: Literal["unverified", "valid", "invalid"]
    last_verified_at: str | None
    # The selected credential source is public metadata, not the credential
    # itself.  A default keeps older integrations that construct profiles
    # positionally source-compatible while the store migrates old databases.
    auth_mode: Literal["password", "cookies"] = "password"


@dataclass(frozen=True, repr=False)
class AccountAuth:
    """Credentials and cookies resolved for one account."""

    username: str
    password: str
    cookies: dict[str, str]
    # ``None`` preserves the legacy in-memory constructor behavior; persisted
    # account rows always resolve to an explicit mode in SQLiteStore.
    auth_mode: Literal["password", "cookies"] | None = None

    def __repr__(self) -> str:
        """Never include account identity or credential material in reprs."""

        return "AccountAuth(<redacted>)"


@dataclass(frozen=True)
class AccountPreferences:
    """Learning preferences scoped to an account."""

    selected_course_ids: list[str] = field(default_factory=list)
    speed: float = 1.0
    jobs: int = 4
    notopen_action: Literal["retry", "continue"] = "retry"
    answer_enabled: bool = False
    answer_cover_rate: float = 0.9
    answer_auto_submit: bool = False
    notification_config: dict = field(default_factory=dict)
    ocr_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerConnection:
    """Shared OpenAI-compatible answer connection settings.

    This is the public model.  It deliberately contains no API-key field;
    callers use ``has_api_key`` to render connection status.
    """

    enabled: bool = False
    base_url: str = "http://localhost:8849/v1"
    model: str = "gemini-3.8-flash-high"
    has_api_key: bool = False
    timeout_seconds: float = 30.0
    max_retries: int = 3
    max_concurrency: int = 4


@dataclass(frozen=True)
class ResolvedAnswerConnection(AnswerConnection):
    """Server-side answer settings with the encrypted key resolved.

    The separate type prevents a public settings read from accidentally
    carrying plaintext and keeps ``AnswerConnection``'s serialized shape
    secret-free.  Its representation and comparisons also omit the key and
    the task-only outbound URL.
    """

    api_key: str | None = field(default=None, repr=False, compare=False)
    # ``base_url`` remains the exact user-visible/stored value.  The task
    # admission boundary may attach this derived value for outbound use only;
    # it is never persisted or returned by a public settings serializer.
    outbound_base_url: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class RuntimeSettings:
    """Process-wide runtime limits."""

    max_active_accounts: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_active_accounts, bool)
            or not isinstance(self.max_active_accounts, int)
            or not 1 <= self.max_active_accounts <= 10
        ):
            raise ValueError("max_active_accounts must be between 1 and 10")


# Task state is deliberately a small closed set.  Keeping the state type in
# the shared model module lets routes, runners, and the manager agree on the
# terminal-state semantics without importing implementation details from one
# another.
TaskState = Literal["running", "stopping", "completed", "failed", "stopped"]


@dataclass(frozen=True)
class TaskSnapshot:
    """Public, credential-free view of one task's current state.

    ``stats`` is copied by :mod:`webapp.task_manager` whenever a snapshot is
    returned.  The dataclass is frozen to make accidental top-level mutation
    difficult, while nested progress metadata remains convenient for JSON
    serializers and existing callers.
    """

    id: str
    account_id: str
    state: TaskState
    progress: int | float = 0
    total: int | float = 0
    current_course: str | None = None
    current_chapter: str | None = None
    current_task: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskDetails:
    """Public, task-scoped course and active-job detail data."""

    courses: list[dict[str, Any]] = field(default_factory=list)
    active_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskLogEntry:
    """One ordered log record retained in a task's bounded buffer."""

    sequence: int
    level: str
    message: str
    timestamp: float


@dataclass(frozen=True)
class TaskLogPage:
    """Cursor response returned by ``TaskManager.get_logs``."""

    items: list[TaskLogEntry] = field(default_factory=list)
    next_cursor: int = 0
