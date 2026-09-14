"""Typed values used by the web application's persistence layer.

The public models intentionally expose only metadata about secrets.  The
store returns the credential-bearing models only from the explicit resolver
methods that need them.
"""

from dataclasses import dataclass, field
from typing import Literal


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


@dataclass(frozen=True)
class AccountAuth:
    """Credentials and cookies resolved for one account."""

    username: str
    password: str
    cookies: dict[str, str]


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
    secret-free.  Its representation and comparisons also omit the key.
    """

    api_key: str | None = field(default=None, repr=False, compare=False)


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
