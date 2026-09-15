"""Shared option parsing for answer providers and quiz submission."""

from __future__ import annotations

import re
from typing import NamedTuple


_BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "【": "】",
    "（": "）",
}
_CLEAR_ENUMERATORS = frozenset({".", "．", ")", "）", "、"})
_SINGLE_ENUMERATORS = _CLEAR_ENUMERATORS | frozenset({",", "，", ":", "："})


# A prefix is a label token followed by punctuation or whitespace.  The
# validation below deliberately keeps the grammar stricter than this shape so
# ordinary text such as ``cat, dog`` is never treated as a label.
_OPTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?P<opening>[\(\[【（])\s*)?"
    r"(?P<label>[A-Za-z]+)"
    r"(?:(?:\s*(?P<separator>[\.．,，、:：\)\]）】]))\s*|(?P<space>\s+))"
)
_OPTION_LABEL_RE = re.compile(
    r"^\s*(?:(?P<opening>[\(\[【（])\s*)?"
    r"(?P<label>[A-Za-z]+)\s*"
    r"(?:(?P<separator>[\.．,，、:：\)\]）】])\s*)?"
    r"(?P<closing>[\]\)】）])?\s*$"
)
_TEXT_PUNCTUATION_RE = re.compile(
    r"[，。！？；：、,.!?;:()（）\[\]【】\"“”‘’\-_\/\\|]"
)


class OptionPrefix(NamedTuple):
    raw_label: str
    end: int
    valid: bool
    punctuated: bool
    separator: str
    bracketed: bool
    opening: str


class OptionEntry(NamedTuple):
    label: str
    text: str
    normalized_text: str


def option_label_for_index(index: int) -> str:
    """Return Excel-style A-Z, AA... labels for a zero-based index."""

    number = index + 1
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def option_lines(options) -> list[str]:
    """Normalize option containers with deterministic ordering."""

    if not options:
        return []
    if isinstance(options, str):
        raw_options = options.splitlines()
    elif isinstance(options, (list, tuple)):
        raw_options = options
    elif isinstance(options, (set, frozenset)):
        raw_options = sorted(options, key=lambda value: str(value))
    else:
        raw_options = [options]
    return [str(option).strip() for option in raw_options if str(option).strip()]


def find_option_prefix(value) -> OptionPrefix | None:
    """Find a label-like prefix and record whether its label is valid.

    Single-letter labels are case-insensitive.  Multi-letter Excel-style
    labels are valid only when written in uppercase (for example ``AA``).
    """

    text = str(value or "").strip()
    match = _OPTION_PREFIX_RE.match(text)
    if not match:
        return None
    raw_label = match.group("label")
    opening = match.group("opening") or ""
    separator = match.group("separator") or ""
    expected_closing = _BRACKET_PAIRS.get(opening)
    bracketed = bool(expected_closing and separator == expected_closing)
    if opening:
        valid = bracketed and (len(raw_label) == 1 or raw_label.isupper())
    elif len(raw_label) == 1:
        valid = not separator or separator in _SINGLE_ENUMERATORS
    else:
        valid = raw_label.isupper() and separator in _CLEAR_ENUMERATORS
    return OptionPrefix(
        raw_label,
        match.end(),
        valid,
        bool(separator),
        separator,
        bracketed,
        opening,
    )


def _prefix_should_be_removed(prefix: OptionPrefix) -> bool:
    if prefix.valid:
        return True
    # A lowercase two-letter Excel-looking prefix (``aa.``) is still an
    # explicit marker whose content should be retained under a generated
    # label.  Longer lowercase words (``cat, dog``) remain ordinary text.
    return (
        prefix.bracketed
        or (
            not prefix.opening
            and prefix.separator in _CLEAR_ENUMERATORS
            and len(prefix.raw_label) <= 2
        )
    )


def strip_option_prefix(value) -> str:
    """Remove a clear option marker while preserving ordinary words."""

    text = str(value or "").strip()
    prefix = find_option_prefix(text)
    if not prefix or not _prefix_should_be_removed(prefix):
        return text
    return text[prefix.end :].strip()


def normalize_choice_text(value, *, strip_prefix: bool = True) -> str:
    """Normalize answer/option text without removing Chinese characters."""

    text = str(value or "").strip()
    if strip_prefix:
        text = strip_option_prefix(text)
    text = re.sub(r"\s+", "", text.casefold())
    return _TEXT_PUNCTUATION_RE.sub("", text)


def _first_unused_label(used_labels: set[str]) -> str:
    index = 0
    while True:
        label = option_label_for_index(index)
        if label not in used_labels:
            return label
        index += 1


def option_entries(options) -> list[OptionEntry]:
    """Parse options into unique labels and normalized display text."""

    entries: list[OptionEntry] = []
    used_labels: set[str] = set()
    for raw_option in option_lines(options):
        prefix = find_option_prefix(raw_option)
        candidate = prefix.raw_label.upper() if prefix and prefix.valid else ""
        if candidate and candidate not in used_labels:
            label = candidate
        else:
            label = _first_unused_label(used_labels)
        used_labels.add(label)

        if prefix and _prefix_should_be_removed(prefix):
            text = raw_option[prefix.end :].strip()
        else:
            text = raw_option
        entries.append(
            OptionEntry(
                label,
                text,
                normalize_choice_text(text, strip_prefix=False),
            )
        )
    return entries


def labeled_option_lines(options) -> tuple[list[str], list[str]]:
    """Return prompt-ready labeled lines and their labels."""

    entries = option_entries(options)
    return (
        [f"{entry.label}. {entry.text}" if entry.text else f"{entry.label}." for entry in entries],
        [entry.label for entry in entries],
    )


def label_from_token(value, valid_labels: set[str]) -> str:
    """Return a valid standalone option label, if present."""

    raw_text = str(value or "").strip()
    match = _OPTION_LABEL_RE.fullmatch(raw_text)
    if not match:
        return ""
    raw_label = match.group("label")
    opening = match.group("opening") or ""
    separator = match.group("separator") or ""
    closing = match.group("closing") or ""
    if closing or (opening and separator != _BRACKET_PAIRS.get(opening)):
        return ""

    if len(raw_label) == 1:
        if opening or separator:
            if opening:
                valid_syntax = separator == _BRACKET_PAIRS.get(opening)
            else:
                valid_syntax = separator in _SINGLE_ENUMERATORS
            if not valid_syntax:
                return ""
        label = raw_label.upper()
    else:
        # A plain exact multi-letter label is the strongest signal and is
        # accepted before considering decorated forms.  Other multi-letter
        # forms must carry an unambiguous bracket/enumerator marker; a comma
        # or colon after a word is ordinary text, not a label.
        if not raw_label.isupper():
            return ""
        bracketed = bool(
            opening and separator == _BRACKET_PAIRS.get(opening)
        )
        clear_enumerator = not opening and separator in _CLEAR_ENUMERATORS
        exact_plain = not opening and not separator and raw_label in valid_labels
        if not (bracketed or clear_enumerator or exact_plain):
            return ""
        label = raw_label
    return label if label in valid_labels else ""


def answer_parts(value) -> list[str]:
    """Split provider answers while preserving ordinary text when possible."""

    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value]
    if isinstance(value, (set, frozenset)):
        return [str(part).strip() for part in sorted(value, key=lambda item: str(item))]
    text = str(value or "").strip()
    if not text:
        return []
    if re.search(r"[\n\r,，、|;；/]", text):
        return [
            part.strip()
            for part in re.split(r"[\n\r,，、|;；/]+", text)
            if part.strip()
        ]
    whitespace_parts = text.split()
    return whitespace_parts if len(whitespace_parts) > 1 else [text]
