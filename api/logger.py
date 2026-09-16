"""Process-wide Loguru configuration and bounded log sanitization."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger
from tqdm import tqdm


tqdm_stream = sys.stderr

# Retain this marker when reading records already erased by older versions.
# New records preserve diagnostic text and redact only identified credentials.
HISTORICAL_SENSITIVE_LOG = "历史敏感日志已清理"

MAX_LOG_MESSAGE_LENGTH = 16_384
MAX_SECRET_LENGTH = 4_096
MAX_SECRET_COUNT = 64
MAX_TASK_ID_LENGTH = 128
MAX_EXTRA_DEPTH = 8
MAX_EXTRA_NODES = 256
MAX_EXTRA_ITEMS = 128
MAX_EXTRA_STRING_LENGTH = 4_096

_LOG_LEVELS = frozenset(
    {"trace", "debug", "info", "success", "warning", "error", "critical"}
)
# Keep this classifier aligned with ``webapp.config_security`` without
# importing the webapp package from the process-wide logger (which would
# create an import cycle during app startup).  Exact aliases and normalized
# segments avoid turning metadata such as ``app_key_id`` into credentials.
_EXTRA_SECRET_ALIASES = frozenset(
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
_EXTRA_DESTINATION_ALIASES = frozenset(
    {"url", "uri", "endpoint", "webhook", "chat", "chat_id", "chatid"}
)
_EXTRA_COMPACT_DESTINATION_VARIANTS = frozenset(
    {
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
_EXTRA_COMPACT_ALIASES = frozenset(
    alias.replace("_", "")
    for alias in _EXTRA_SECRET_ALIASES | _EXTRA_DESTINATION_ALIASES
) | _EXTRA_COMPACT_DESTINATION_VARIANTS
_EXTRA_SECRET_MARKERS = frozenset(
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
_EXTRA_DESTINATION_MARKERS = frozenset(
    {"url", "uri", "endpoint", "webhook", "chat"}
)
_EXTRA_METADATA_SUFFIXES = frozenset(
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

_URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s<>\[\]{}\"']+")

# Header-like values are handled separately from prose.  A bare word such as
# ``token bucket progress`` is ordinary progress text and is not an
# assignment, so it remains visible.
_HEADER_KEY = (
    r"set[-_ ]?cookie|cookie(?:s)?|authorization|bearer|dtoken|jtoken|"
    r"ktoken|token|api[-_ ]?key|access[-_ ]?token|secret|password|passwd|"
    r"credential"
)
_AUTH_COOKIE_KEY = r"set[-_ ]?cookie|cookie(?:s)?|authorization"
_AUTH_COOKIE_QUOTED_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])['\"]?(?:{_AUTH_COOKIE_KEY})['\"]?"
    rf"\s*[:=]\s*)(?P<quote>['\"])(?P<value>[^'\"\r\n]{{0,{MAX_LOG_MESSAGE_LENGTH}}})"
    rf"(?P=quote)"
)
_AUTH_COOKIE_BARE_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])['\"]?(?:{_AUTH_COOKIE_KEY})['\"]?"
    rf"\s*[:=]\s*)(?P<value>[^\r\n,{{}}\[\]]{{1,{MAX_LOG_MESSAGE_LENGTH}}})"
)
_HEADER_QUOTED_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])['\"]?(?!(?:{_AUTH_COOKIE_KEY})\b)"
    rf"(?:{_HEADER_KEY})['\"]?\s*[:=]\s*)"
    rf"(?P<quote>['\"])(?P<value>[^'\"\r\n]{{0,{MAX_LOG_MESSAGE_LENGTH}}})"
    rf"(?P=quote)"
)
_HEADER_BARE_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])['\"]?(?!(?:{_AUTH_COOKIE_KEY})\b)"
    rf"(?:{_HEADER_KEY})['\"]?\s*[:=]\s*)"
    rf"(?P<value>[^\s,;{{}}\[\]]{{1,{MAX_LOG_MESSAGE_LENGTH}}})"
)
_AUTHORIZATION_BEARER_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])authorization\s+bearer\s+)"
    rf"(?P<quote>['\"]?)(?!\[redacted\])"
    rf"(?P<value>[^\s,;{{}}\[\]]{{1,{MAX_LOG_MESSAGE_LENGTH}}})"
    rf"(?P=quote)"
)
_BEARER_PATTERN = re.compile(
    rf"(?ix)(?P<prefix>(?<![\w-])bearer\s+)"
    rf"(?P<quote>['\"]?)(?!\[redacted\])"
    rf"(?P<value>[^\s,;{{}}\[\]]{{1,{MAX_LOG_MESSAGE_LENGTH}}})"
    rf"(?P=quote)"
)

# Synthetic/provider values often contain a separator around a credential
# word.  Requiring that separator prevents the ordinary word ``token`` from
# matching unrelated progress prose.
_SECRET_WORD_PATTERN = re.compile(
    r"(?i)(?<![\w])(?!(?:set[-_. ]?cookie)\b)(?:"
    r"(?:[^\W_.-]+[-_.]){1,64}(?:secret|dtoken|jtoken|ktoken|token|cookie|bearer)"
    r"[\w.-]*+|"
    r"(?:secret|dtoken|jtoken|ktoken|token|cookie|bearer)[-_.][\w.-]*+|"
    r"(?:api[_-]?key|access[-_]?token)[-_.][\w.-]*+"
    r")(?![\w])"
)

_TRUNCATION_MARKER = "[truncated]"
_PROTECTED_MARKER_PATTERN = re.compile(
    r"\[(?:redacted(?:-[a-z]+)*|truncated|unavailable(?:-[a-z]+)?)\]"
)


def validate_task_id(value: Any) -> str:
    """Validate a task routing identifier without changing its spelling."""

    if not isinstance(value, str):
        raise TypeError("task_id must be a string")
    if not value or len(value) > MAX_TASK_ID_LENGTH or value.strip() != value:
        raise ValueError("task_id has an invalid length or surrounding whitespace")
    return value


def _normalise_secrets(secrets: Iterable[Any] | None) -> tuple[str, ...]:
    """Return deterministic literal secrets within the message-size bound.

    ``MAX_SECRET_LENGTH`` is the normal collector budget, but dropping a
    caller-supplied value just because it is a little larger is unsafe: the
    beginning of that value can then survive message truncation.  Keep a
    bounded prefix for larger values (the whole value for any message-sized
    secret) so matching remains resource bounded without silently disabling
    redaction.
    """

    if secrets is None:
        return ()
    if isinstance(secrets, (str, bytes, bytearray)):
        secrets = (secrets,)
    values: list[str] = []
    try:
        iterator = iter(secrets)
    except TypeError:
        iterator = iter((secrets,))
    except BaseException:
        return ()
    try:
        for value in iterator:
            if len(values) >= MAX_SECRET_COUNT:
                break
            if value is None or isinstance(value, (bytes, bytearray, memoryview)):
                continue
            try:
                text = str(value)
            except BaseException:
                continue
            if not text:
                continue
            if len(text) > MAX_LOG_MESSAGE_LENGTH:
                text = text[:MAX_LOG_MESSAGE_LENGTH]
            values.append(text)
    except BaseException:
        # A caller-owned iterator is outside the logging trust boundary.  A
        # broken ``__next__`` must not turn a best-effort sanitizer into the
        # application exception path.
        pass
    return tuple(sorted(set(values), key=lambda item: (-len(item), item)))


def _coerce_message(value: Any) -> str:
    """Coerce only intentional message values, never arbitrary object reprs."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        return "[redacted-bytes]"
    elif isinstance(value, BaseException):
        try:
            text = str(value)
        except BaseException:
            return "[unavailable]"
    elif value is None:
        return ""
    elif isinstance(value, (bool, int, float)):
        text = str(value)
    else:
        return "[redacted-object]"
    return text


def _secret_scan_limit(secrets: tuple[str, ...]) -> int:
    """Return the largest raw prefix that can affect a bounded log output.

    A secret beginning in the retained ``MAX_LOG_MESSAGE_LENGTH`` prefix may
    extend past that boundary.  Include one complete normalized secret after
    the boundary so a literal match is found before the output is truncated.
    Anything beyond this window is discarded, never copied into the result.
    """

    longest = max((len(secret) for secret in secrets), default=0)
    if not longest:
        return MAX_LOG_MESSAGE_LENGTH
    return MAX_LOG_MESSAGE_LENGTH + longest - 1


def _replace_known_secrets(
    text: str,
    secrets: tuple[str, ...],
    *,
    output_limit: int | None = None,
) -> str:
    """Replace literal secrets with bounded per-secret searches.

    The message passed by :func:`sanitize_log_message` is already limited to
    ``_secret_scan_limit``.  Searching each of at most 64 literals avoids an
    Aho-Corasick trie whose node count scales with the sum of secret lengths;
    both working memory and worst-case scan work remain bounded by the input
    window and the configured secret count.  ``output_limit`` truncates by
    source position, before replacements can shift a suffix into view.
    """

    source_limit = len(text)
    if output_limit is not None:
        source_limit = min(source_limit, max(0, output_limit))
    if not text or not secrets:
        return text[:source_limit]

    # Difference marks keep memory proportional to the bounded text window,
    # even when many short secrets overlap repeatedly in the same input.
    coverage = [0] * (len(text) + 1)
    found = False
    for secret in secrets:
        secret_length = len(secret)
        if not secret_length or secret_length > len(text):
            continue
        start = 0
        while True:
            match = text.find(secret, start)
            if match < 0:
                break
            coverage[match] += 1
            coverage[match + secret_length] -= 1
            found = True
            # Include overlapping occurrences.  The interval merge below
            # collapses runs such as ``aaaa`` with secret ``aa`` safely.
            start = match + 1
    if not found:
        return text[:source_limit]

    # Generated markers are already safe.  Keeping their spans immutable is
    # what makes a second sanitizer pass stable even when a secret is a
    # character contained in ``[redacted]`` (for example ``"a"``).
    protected = [(match.start(), match.end()) for match in _PROTECTED_MARKER_PATTERN.finditer(text)]

    redactions: list[tuple[int, int]] = []
    protected_index = 0
    active = 0
    redaction_start: int | None = None
    for index in range(len(text)):
        active += coverage[index]
        while (
            protected_index < len(protected)
            and protected[protected_index][1] <= index
        ):
            protected_index += 1
        is_protected = (
            protected_index < len(protected)
            and protected[protected_index][0] <= index < protected[protected_index][1]
        )
        should_redact = active > 0 and not is_protected
        if should_redact and redaction_start is None:
            redaction_start = index
        elif not should_redact and redaction_start is not None:
            redactions.append((redaction_start, index))
            redaction_start = None
    if redaction_start is not None:
        redactions.append((redaction_start, len(text)))

    if not redactions:
        return text[:source_limit]
    output: list[str] = []
    cursor = 0
    for start, end in redactions:
        if start >= source_limit:
            break
        output.append(text[cursor:start])
        output.append("[redacted]")
        cursor = min(end, source_limit)
        if end > source_limit:
            break
    output.append(text[cursor:source_limit])
    return "".join(output)


def _replace_urls(text: str) -> str:
    """Remove complete URLs so userinfo/query/path tokens cannot survive."""

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        trailing = ""
        while value and value[-1] in ".,;:!?，。；：！？":
            trailing = value[-1] + trailing
            value = value[:-1]
        return "[redacted-url]" + trailing

    return _URL_PATTERN.sub(replace, text)


def _replace_secret_assignments(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}[redacted]"

    for pattern in (
        _AUTH_COOKIE_QUOTED_PATTERN,
        _AUTH_COOKIE_BARE_PATTERN,
        _HEADER_QUOTED_PATTERN,
        _HEADER_BARE_PATTERN,
        _AUTHORIZATION_BEARER_PATTERN,
        _BEARER_PATTERN,
    ):
        text = pattern.sub(replace, text)
    return text


def _bound_log_message(text: str) -> str:
    """Keep sanitizer output bounded without cutting a generated marker."""

    return _bound_text(text, MAX_LOG_MESSAGE_LENGTH)


def _bound_text(text: str, limit: int) -> str:
    """Bound text while preserving complete generated markers."""

    if len(text) <= limit:
        return text
    prefix = text[: limit - len(_TRUNCATION_MARKER)]
    # A partial marker would be eligible for replacement on a later pass and
    # would violate idempotence.  Discard that incomplete tail before adding
    # the complete truncation marker.
    if prefix.rfind("[") > prefix.rfind("]"):
        prefix = prefix[: prefix.rfind("[")]
    return prefix + _TRUNCATION_MARKER


def sanitize_log_message(
    value: Any,
    *,
    secrets: Iterable[Any] | None = None,
    historical: bool = False,
) -> str:
    """Return a bounded log-safe message.

    Current messages retain harmless operational text while removing literal
    credentials, header values, URL spans, and synthetic credential words.
    The historical keyword remains for caller compatibility. Stored and live
    records follow the same rules so persistence never erases useful progress
    or failure reasons simply because their wording is unfamiliar.
    """

    secret_values = _normalise_secrets(secrets)
    text = _coerce_message(value)
    if not text:
        return ""
    if text == HISTORICAL_SENSITIVE_LOG:
        return text

    source_over_limit = len(text) > MAX_LOG_MESSAGE_LENGTH
    scan_limit = _secret_scan_limit(secret_values)
    was_scan_truncated = len(text) > scan_limit
    if was_scan_truncated:
        # A secret can cross the public output boundary, so keep enough raw
        # suffix to find it.  Never process or copy arbitrary data past this
        # strict window.
        text = text[:scan_limit]

    text = _replace_known_secrets(
        text,
        secret_values,
        output_limit=MAX_LOG_MESSAGE_LENGTH if source_over_limit else None,
    )
    text = _replace_urls(text)
    text = _replace_secret_assignments(text)
    text = _SECRET_WORD_PATTERN.sub("[redacted]", text)
    if source_over_limit:
        text += _TRUNCATION_MARKER
    return _bound_log_message(text)


def sanitize_log_level(value: Any) -> str:
    """Map arbitrary level values to the closed public level set."""

    if isinstance(value, str):
        candidate = value.strip().lower()
    else:
        candidate = ""
    return candidate if candidate in _LOG_LEVELS else "info"


def _safe_extra_key(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        return "[unavailable-key]"
    return text[:MAX_EXTRA_STRING_LENGTH]


def _normalise_extra_key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_sensitive_extra_key(value: str) -> bool:
    """Classify credential and private-destination extra keys safely."""

    normalized = _normalise_extra_key(value)
    compact = normalized.replace("_", "")
    if not normalized:
        return False
    if normalized in _EXTRA_SECRET_ALIASES | _EXTRA_DESTINATION_ALIASES:
        return True
    if compact in _EXTRA_COMPACT_ALIASES:
        return True

    parts = tuple(part for part in normalized.split("_") if part)
    suffix = parts[-1] if parts else ""
    for marker in _EXTRA_SECRET_MARKERS | _EXTRA_DESTINATION_MARKERS:
        if marker not in parts:
            continue
        if marker == "chat" and suffix == "id":
            return True
        if suffix in _EXTRA_METADATA_SUFFIXES and suffix != marker:
            continue
        return True
    return False


def _sanitize_extra(
    value: Any,
    secrets: tuple[str, ...],
    *,
    key: str = "",
    depth: int = 0,
    state: dict[str, Any] | None = None,
) -> Any:
    """Sanitize nested extras with cycle/depth/node/size limits."""

    if state is None:
        state = {"nodes": 0, "active": set()}
    key_name = _normalise_extra_key(key)
    if key_name == "task_id":
        try:
            return validate_task_id(value)
        except (TypeError, ValueError):
            return None
    if _is_sensitive_extra_key(key):
        return "[redacted]"
    if depth > state.get("max_depth", MAX_EXTRA_DEPTH) or state["nodes"] >= state.get("max_nodes", MAX_EXTRA_NODES):
        return "[redacted-depth]"
    state["nodes"] += 1

    if isinstance(value, str):
        value = sanitize_log_message(value, secrets=secrets)
        return _bound_text(value, MAX_EXTRA_STRING_LENGTH)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[redacted-bytes]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else "[redacted-number]"
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in state["active"]:
            return "[redacted-cycle]"
        state["active"].add(marker)
        result: dict[str, Any] = {}
        try:
            for index, (item_key, item_value) in enumerate(value.items()):
                if index >= state.get("max_items", MAX_EXTRA_ITEMS):
                    result["[truncated-items]"] = "[redacted-size]"
                    break
                safe_key = _safe_extra_key(item_key)
                result[safe_key] = _sanitize_extra(
                    item_value,
                    secrets,
                    key=safe_key,
                    depth=depth + 1,
                    state=state,
                )
        except BaseException:
            return "[redacted-object]"
        finally:
            state["active"].discard(marker)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        marker = id(value)
        if marker in state["active"]:
            return "[redacted-cycle]"
        state["active"].add(marker)
        result: list[Any] = []
        try:
            for index, item in enumerate(value):
                if index >= state.get("max_items", MAX_EXTRA_ITEMS):
                    result.append("[redacted-size]")
                    break
                result.append(
                    _sanitize_extra(
                        item,
                        secrets,
                        key=key,
                        depth=depth + 1,
                        state=state,
                    )
                )
        except BaseException:
            return "[redacted-object]"
        finally:
            state["active"].discard(marker)
        return result
    if isinstance(value, BaseException):
        return sanitize_log_message(value, secrets=secrets)
    return "[redacted-object]"


def sanitize_log_extra(value: Any, *, secrets: Iterable[Any] | None = None) -> Any:
    """Sanitize the small extra metadata attached to a log record."""

    return _sanitize_extra(value, _normalise_secrets(secrets))


def sanitize_monitor_value(value: Any, *, secrets: Iterable[Any] | None = None) -> Any:
    """Copy a complete course catalog without applying log-record limits.

    A semester contains thousands of course/chapter/job fields. The small
    Loguru extra budget silently destroyed those fields on every read/write.
    Keep cycle protection and credential redaction, with room for real course
    trees and their persistence envelope.
    """
    return _sanitize_extra(value, _normalise_secrets(secrets), state={
        "nodes": 0, "active": set(),
        "max_depth": 16, "max_nodes": 1_000_000, "max_items": 100_000,
    })


def _sanitize_record(record: dict[str, Any]) -> None:
    """Loguru patcher applied before every configured sink receives a record."""

    try:
        extra = record.get("extra")
        if not isinstance(extra, Mapping):
            extra = {}
        secrets = _normalise_secrets(extra.get("_log_secrets", ()))
        sanitized: dict[str, Any] = {}
        for key, value in extra.items():
            try:
                safe_key = _safe_extra_key(key)
            except BaseException:
                safe_key = "[unavailable-key]"
            if safe_key == "_log_secrets":
                continue
            sanitized[safe_key] = _sanitize_extra(value, secrets, key=safe_key)
        record["message"] = sanitize_log_message(
            record.get("message", ""), secrets=secrets
        )
        record["extra"] = sanitized
        # Tracebacks retain exception values and locals, so no exception object
        # may cross a configured sink.  The fixed message above remains.
        record["exception"] = None
    except BaseException:
        task_id = None
        try:
            task_id = validate_task_id(record.get("extra", {}).get("task_id"))
        except BaseException:
            pass
        record["message"] = "日志记录已清理"
        record["extra"] = {"task_id": task_id} if task_id else {}
        record["exception"] = None


def tqdm_sink(msg):
    tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()


logger.remove()
logger.configure(patcher=_sanitize_record)
level = os.environ.get("CHAOXING_LOG_LEVEL", "INFO").strip().upper()
if level not in {item.upper() for item in _LOG_LEVELS}:
    level = "INFO"
logger.add(tqdm_sink, colorize=True, enqueue=False, level=level)
data_dir = Path(os.environ.get("CHAOXING_DATA_DIR", ".")).expanduser()
data_dir.mkdir(parents=True, exist_ok=True)
log_path = data_dir / "chaoxing.log"
logger.add(
    str(log_path),
    rotation="10 MB",
    retention="7 days",
    level=level,
    encoding="utf-8",
)
try:
    log_path.chmod(0o600)
except OSError:
    pass


__all__ = [
    "HISTORICAL_SENSITIVE_LOG",
    "MAX_EXTRA_DEPTH",
    "MAX_EXTRA_ITEMS",
    "MAX_EXTRA_NODES",
    "MAX_LOG_MESSAGE_LENGTH",
    "MAX_TASK_ID_LENGTH",
    "logger",
    "sanitize_log_extra",
    "sanitize_log_level",
    "sanitize_log_message",
    "sanitize_monitor_value",
    "validate_task_id",
]
