"""Input-size limits shared by the Web routes and persistence layer.

The Flask request limit is intentionally only the outermost guard.  These
smaller limits keep a single account, cookie jar, or provider configuration
from filling the SQLite database (or consuming excessive CPU while it is
being encrypted/serialized).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MAX_ACCOUNT_NAME_LENGTH = 200
MAX_ACCOUNT_USERNAME_LENGTH = 256
MAX_ACCOUNT_PASSWORD_LENGTH = 4096

MAX_COOKIE_COUNT = 256
MAX_COOKIE_NAME_LENGTH = 256
MAX_COOKIE_VALUE_LENGTH = 8192
MAX_COOKIE_HEADER_LENGTH = 64 * 1024

MAX_SELECTED_COURSE_IDS = 1000
MAX_COURSE_ID_LENGTH = 256

# Notification/OCR settings are provider-specific, so do not impose a fixed
# schema.  Bound their shape and scalar values instead.
MAX_CONFIG_DEPTH = 8
MAX_CONFIG_NODES = 1024
MAX_CONFIG_KEY_LENGTH = 128
MAX_CONFIG_STRING_LENGTH = 8192
MAX_CONFIG_LIST_LENGTH = 256
MAX_CONFIG_TOTAL_CHARS = 64 * 1024


def validate_config_shape(value: Any, *, field_name: str = "config") -> None:
    """Reject oversized/unrepresentable nested provider configuration.

    Only JSON-like values are accepted.  This mirrors what Flask can receive
    and prevents direct store callers from bypassing the request-size guard.
    ``ValueError`` is used for all shape/size failures so route and store
    callers can present their existing stable validation response.
    """

    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")

    nodes = 0
    chars = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal nodes, chars
        nodes += 1
        if nodes > MAX_CONFIG_NODES:
            raise ValueError(f"{field_name} is too large")
        if depth > MAX_CONFIG_DEPTH:
            raise ValueError(f"{field_name} is too deeply nested")

        if isinstance(item, Mapping):
            if len(item) > MAX_CONFIG_NODES:
                raise ValueError(f"{field_name} is too large")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > MAX_CONFIG_KEY_LENGTH:
                    raise ValueError(f"{field_name} contains an invalid key")
                chars += len(key)
                if chars > MAX_CONFIG_TOTAL_CHARS:
                    raise ValueError(f"{field_name} is too large")
                walk(child, depth + 1)
            return

        if isinstance(item, (list, tuple)):
            if len(item) > MAX_CONFIG_LIST_LENGTH:
                raise ValueError(f"{field_name} list is too large")
            for child in item:
                walk(child, depth + 1)
            return

        if isinstance(item, str):
            if len(item) > MAX_CONFIG_STRING_LENGTH:
                raise ValueError(f"{field_name} contains an oversized string")
            chars += len(item)
            if chars > MAX_CONFIG_TOTAL_CHARS:
                raise ValueError(f"{field_name} is too large")
            return

        if item is None or isinstance(item, (bool, int, float)):
            return

        raise ValueError(f"{field_name} contains an unsupported value")

    walk(value, 0)


def validate_cookie_mapping(value: Any, *, field_name: str = "cookies") -> dict[str, str]:
    """Normalize and validate a cookie mapping before encryption."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    if len(value) > MAX_COOKIE_COUNT:
        raise ValueError(f"{field_name} contains too many cookies")
    normalized: dict[str, str] = {}
    total = 0
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        item = str(raw_value)
        if not key or len(key) > MAX_COOKIE_NAME_LENGTH:
            raise ValueError(f"{field_name} contains an invalid cookie name")
        if len(item) > MAX_COOKIE_VALUE_LENGTH:
            raise ValueError(f"{field_name} contains an oversized cookie")
        total += len(key) + len(item)
        if total > MAX_COOKIE_HEADER_LENGTH:
            raise ValueError(f"{field_name} is too large")
        normalized[key] = item
    return normalized


def validate_text(value: Any, max_length: int, *, field_name: str) -> str:
    """Validate one required/secret text field and return its string value."""

    if not isinstance(value, str) or len(value) > max_length:
        raise ValueError(f"{field_name} is too long")
    return value


__all__ = [
    "MAX_ACCOUNT_NAME_LENGTH",
    "MAX_ACCOUNT_USERNAME_LENGTH",
    "MAX_ACCOUNT_PASSWORD_LENGTH",
    "MAX_COOKIE_COUNT",
    "MAX_COOKIE_NAME_LENGTH",
    "MAX_COOKIE_VALUE_LENGTH",
    "MAX_COOKIE_HEADER_LENGTH",
    "MAX_SELECTED_COURSE_IDS",
    "MAX_COURSE_ID_LENGTH",
    "MAX_CONFIG_DEPTH",
    "MAX_CONFIG_NODES",
    "MAX_CONFIG_KEY_LENGTH",
    "MAX_CONFIG_STRING_LENGTH",
    "MAX_CONFIG_LIST_LENGTH",
    "MAX_CONFIG_TOTAL_CHARS",
    "validate_config_shape",
    "validate_cookie_mapping",
    "validate_text",
]
