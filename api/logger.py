"""Process-wide Loguru configuration and bounded log sanitization."""

from __future__ import annotations

import os
import re
import sys
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger
from tqdm import tqdm


tqdm_stream = sys.stderr

# This marker is deliberately non-diagnostic.  It is also the canonical value
# used when repairing an old record whose provenance is not safe to inspect.
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
_SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "cookie",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "push_key",
    "pushkey",
    "app_key",
    "appkey",
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

_HISTORICAL_CONTENT_PATTERN = re.compile(
    r"(?ix)"
    r"原始标题|处理后标题|从缓存中获取答案|获取答案失败|成功获取到答案|"
    r"找到答案|随机选择|填写答案|当前题目信息|响应内容|原始卡片|任务数据|"
    r"(?:题干|选项|答案)\s*[:：=]|"
    r"\b(?:question|options?|answer|card|response(?:[ _-]*(?:body|text))?)"
    r"\s*[:=]"
)
_HISTORICAL_RESULT_PATTERN = re.compile(
    r"(?i)答题(?:成功|失败)|阅读任务学习\s*[-=:>]|"
    r"空页面任务(?:完成|失败).*[-=:>]|(?:content|body|text)\s*[:=]"
)
_HISTORICAL_QUERY_PATTERN = re.compile(r"获取答案\s*[:：=]|答案\s*(?:->|为)")
_HISTORICAL_HEADER_PATTERN = re.compile(
    r"(?ix)\b(?:raw\s+)?headers?\b|\b(?:raw\s+)?response\b|"
    r"\b(?:request|response)[-_ ]?(?:body|text|data)\b|"
    r"\b(?:raw|url|uri|dtoken|jtoken|ktoken|card|mapping|dict)\b\s*[:=]"
)
_HISTORICAL_JSON_PATTERN = re.compile(r"(?s)^\s*[\[{].{0,16384}[\]}]\s*$")

# Explicit allowlist for harmless legacy runtime labels.  Dynamic values must
# be structured (and contain a digit); arbitrary prose after ``progress`` or
# another operational prefix is replaced as a whole by the marker below.
_HISTORICAL_SAFE_PATTERN = re.compile(
    r"(?x)^(?:"
    r"服务重启导致任务中断，超星进度保留，可重新开始继续|"
    r"(?:completed\s+)?checkpoint(?:[\s_-]+[a-z0-9_.:/-]+)*|"
    r"(?:first|second)-only|entry-[0-9]+|kept|ignored|"
    r"(?:progress|retry|attempt|queued|running|stopping|worker|task|job|"
    r"chapter|course)(?:[\s_:#/().+%=-]+(?=[a-z0-9_.:/%+-]*\d)[a-z0-9_.:/%+-]+)*|"
    r"(?:任务进度|任务开始|任务完成|任务停止|任务失败|队列状态|工作线程状态)"
    r"(?:[：: /_-]+(?=[0-9a-z_.:/%+-]*\d)[0-9a-z_.:/%+-]+)*"
    r")$"
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
    if len(text) > MAX_LOG_MESSAGE_LENGTH:
        limit = MAX_LOG_MESSAGE_LENGTH - len(_TRUNCATION_MARKER)
        return text[:limit] + _TRUNCATION_MARKER
    return text


def _replace_known_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """Replace literal secrets in one bounded Aho-Corasick pass."""

    if not text or not secrets:
        return text
    transitions: list[dict[str, int]] = [{}]
    failures = [0]
    longest = [0]
    for secret in secrets:
        state = 0
        for char in secret:
            next_state = transitions[state].get(char)
            if next_state is None:
                next_state = len(transitions)
                transitions[state][char] = next_state
                transitions.append({})
                failures.append(0)
                longest.append(0)
            state = next_state
        longest[state] = max(longest[state], len(secret))

    queue: deque[int] = deque(transitions[0].values())
    while queue:
        state = queue.popleft()
        for char, child in transitions[state].items():
            queue.append(child)
            fallback = failures[state]
            while fallback and char not in transitions[fallback]:
                fallback = failures[fallback]
            failures[child] = transitions[fallback].get(char, 0)
            longest[child] = max(longest[child], longest[failures[child]])

    matches: dict[int, int] = {}
    state = 0
    for index, char in enumerate(text):
        while state and char not in transitions[state]:
            state = failures[state]
        state = transitions[state].get(char, 0)
        length = longest[state]
        if length:
            start = index - length + 1
            if start >= 0:
                matches[start] = max(matches.get(start, 0), length)
    if not matches:
        return text

    # Generated markers are already safe.  Keeping their spans immutable is
    # what makes a second sanitizer pass stable even when a secret is a
    # character contained in ``[redacted]`` (for example ``"a"``).
    protected = [(match.start(), match.end()) for match in _PROTECTED_MARKER_PATTERN.finditer(text)]

    redactions: list[tuple[int, int]] = []
    protected_index = 0
    for start, length in sorted(matches.items()):
        end = start + length
        while protected_index < len(protected) and protected[protected_index][1] <= start:
            protected_index += 1
        if protected_index < len(protected):
            protected_start, protected_end = protected[protected_index]
            if start < protected_end and end > protected_start:
                continue
        if redactions and start <= redactions[-1][1]:
            redactions[-1] = (redactions[-1][0], max(redactions[-1][1], end))
        else:
            redactions.append((start, end))

    if not redactions:
        return text
    output: list[str] = []
    cursor = 0
    for start, end in redactions:
        output.append(text[cursor:start])
        output.append("[redacted]")
        cursor = end
    output.append(text[cursor:])
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

    if len(text) <= MAX_LOG_MESSAGE_LENGTH:
        return text
    limit = MAX_LOG_MESSAGE_LENGTH - len(_TRUNCATION_MARKER)
    prefix = text[:limit]
    # A partial marker would be eligible for replacement on a later pass and
    # would violate idempotence.  Discard that incomplete tail before adding
    # the complete truncation marker.
    if prefix.rfind("[") > prefix.rfind("]"):
        prefix = prefix[: prefix.rfind("[")]
    return prefix + _TRUNCATION_MARKER


def _historical_has_sensitive_shape(text: str) -> bool:
    return bool(
        _URL_PATTERN.search(text)
        or _SECRET_WORD_PATTERN.search(text)
        or _AUTH_COOKIE_QUOTED_PATTERN.search(text)
        or _AUTH_COOKIE_BARE_PATTERN.search(text)
        or _HEADER_QUOTED_PATTERN.search(text)
        or _HEADER_BARE_PATTERN.search(text)
        or _AUTHORIZATION_BEARER_PATTERN.search(text)
        or _BEARER_PATTERN.search(text)
        or _HISTORICAL_CONTENT_PATTERN.search(text)
        or _HISTORICAL_RESULT_PATTERN.search(text)
        or _HISTORICAL_QUERY_PATTERN.search(text)
        or _HISTORICAL_HEADER_PATTERN.search(text)
        or _HISTORICAL_JSON_PATTERN.match(text)
    )


def sanitize_log_message(
    value: Any,
    *,
    secrets: Iterable[Any] | None = None,
    historical: bool = False,
) -> str:
    """Return a bounded log-safe message.

    Current messages retain harmless operational text while removing literal
    credentials, header values, URL spans, and synthetic credential words.
    Historical records use an explicit allowlist; unknown old free text is
    replaced by a stable marker so a restart cannot expose its provenance.
    """

    text = _coerce_message(value)
    if not text:
        return ""
    if text == HISTORICAL_SENSITIVE_LOG:
        return text
    secret_values = _normalise_secrets(secrets)
    if historical and (
        any(secret in text for secret in secret_values)
        or _historical_has_sensitive_shape(text)
    ):
        return HISTORICAL_SENSITIVE_LOG

    text = _replace_known_secrets(text, secret_values)
    text = _replace_urls(text)
    text = _replace_secret_assignments(text)
    text = _SECRET_WORD_PATTERN.sub("[redacted]", text)
    if historical and not _HISTORICAL_SAFE_PATTERN.fullmatch(text):
        return HISTORICAL_SENSITIVE_LOG
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
    key_name = key.strip().lower().replace("-", "_")
    if key_name == "task_id":
        try:
            return validate_task_id(value)
        except (TypeError, ValueError):
            return None
    if any(marker in key_name for marker in _SENSITIVE_KEY_MARKERS):
        return "[redacted]"
    if depth > MAX_EXTRA_DEPTH or state["nodes"] >= MAX_EXTRA_NODES:
        return "[redacted-depth]"
    state["nodes"] += 1

    if isinstance(value, str):
        if len(value) > MAX_EXTRA_STRING_LENGTH:
            value = value[:MAX_EXTRA_STRING_LENGTH] + "[truncated]"
        return sanitize_log_message(value, secrets=secrets)
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
                if index >= MAX_EXTRA_ITEMS:
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
                if index >= MAX_EXTRA_ITEMS:
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
    """Sanitize a nested value for task details or a custom Loguru sink."""

    return _sanitize_extra(value, _normalise_secrets(secrets))


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
    "validate_task_id",
]
