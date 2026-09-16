"""Shared provider-config key classification for public and worker boundaries."""

from __future__ import annotations

import re
from typing import Any


_CONFIG_SECRET_ALIASES = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "cookie",
        "cookies",
        "authorization",
        "private_key",
        "push_key",
        "app_key",
        "auth",
        "sign",
        "signature",
    }
)
_CONFIG_DESTINATION_ALIASES = frozenset(
    {"url", "uri", "endpoint", "webhook", "chat", "chat_id", "chatid"}
)
_CONFIG_COMPACT_SECRET_ALIASES = frozenset(
    alias.replace("_", "") for alias in _CONFIG_SECRET_ALIASES
)
_CONFIG_COMPACT_DESTINATION_ALIASES = frozenset(
    alias.replace("_", "") for alias in _CONFIG_DESTINATION_ALIASES
)
_CONFIG_SECRET_MARKERS = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "cookie",
        "cookies",
        "authorization",
        "private",
        "auth",
        "sign",
        "signature",
    }
)
_CONFIG_DESTINATION_MARKERS = frozenset(
    {"url", "uri", "endpoint", "webhook", "chat"}
)
_CONFIG_COMPACT_DESTINATION_VARIANTS = frozenset(
    {
        # A few established provider/legacy spellings omit separators
        # entirely, so retain those explicitly without falling back to broad
        # substring matching (which would classify ``monkey`` as a secret).
        "baseurl",
        "callbackurl",
        "callbackuri",
        "fallbackendpoint",
        "httpendpoint",
        "notifyurl",
        "ocrendpoint",
        "resourceuri",
        "serviceendpoint",
        "serviceurl",
        "tgchatid",
        "webhookurl",
    }
)
_CONFIG_METADATA_SUFFIXES = frozenset(
    {
        "count",
        "description",
        "enabled",
        "field",
        "id",
        "index",
        "label",
        "method",
        "mode",
        "name",
        "number",
        "scheme",
        "title",
        "type",
    }
)


def normalize_config_key(key: Any) -> str:
    """Normalize snake, kebab, camel and compact config aliases."""

    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def is_sensitive_config_key(
    key: Any,
    *,
    notification: bool = False,
    include_destinations: bool | None = None,
) -> bool:
    """Recognize bounded credential and private-destination config aliases.

    Provider maps use different spellings, including ``push_key``,
    ``app_key``, ``auth``, ``sign`` and ``signature``.  Segment-based matching
    protects compound forms such as ``authorization_header`` and
    ``callback_url`` without classifying an unrelated display key like
    ``monkey``.  Private destinations are sensitive when the caller opts into
    them for either notification or OCR responses.  ``notification`` remains
    a compatibility keyword for callers of the former notification-only
    helper; callers that process OCR responses can pass
    ``include_destinations=True`` without changing the input/update semantics
    of the old ``notification=False`` mode.
    """

    if include_destinations is None:
        include_destinations = notification
    normalized = normalize_config_key(key)
    compact = normalized.replace("_", "")
    if not normalized:
        return False
    if normalized in _CONFIG_SECRET_ALIASES:
        return True
    if include_destinations and normalized in _CONFIG_DESTINATION_ALIASES:
        return True
    # Support common legacy spellings that omit separators, while keeping the
    # accepted set explicit instead of matching arbitrary substrings.
    compact_aliases = _CONFIG_COMPACT_SECRET_ALIASES
    if include_destinations:
        compact_aliases = (
            compact_aliases
            | _CONFIG_COMPACT_DESTINATION_ALIASES
            | _CONFIG_COMPACT_DESTINATION_VARIANTS
        )
    if compact in compact_aliases:
        return True

    parts = tuple(part for part in normalized.split("_") if part)
    if not parts:
        return False
    suffix = parts[-1]
    markers = _CONFIG_SECRET_MARKERS
    if include_destinations:
        markers = markers | _CONFIG_DESTINATION_MARKERS
    for marker in markers:
        if marker not in parts:
            continue
        if marker == "chat" and suffix == "id":
            return True
        # A field explicitly named ``auth_mode``/``token_type``/etc. is
        # metadata rather than the credential itself.  Other compound forms
        # (``authorization_header``, ``callback_url``, ``signature_value``)
        # remain protected.
        if suffix in _CONFIG_METADATA_SUFFIXES and suffix != marker:
            continue
        return True
    return False


__all__ = ["is_sensitive_config_key", "normalize_config_key"]
