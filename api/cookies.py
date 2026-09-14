# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Mapping

import requests

from api.config import GlobalConst as gc


def load_cookie_file(path: str | Path = gc.COOKIES_PATH) -> dict[str, str]:
    """Load the semicolon-delimited cookie format used by the CLI.

    Cookie values may themselves contain ``=`` characters, so split each
    entry only at its first separator.  Missing and empty files represent an
    empty cookie jar.
    """

    cookie_path = Path(path)
    if not cookie_path.exists():
        return {}

    cookies: dict[str, str] = {}
    content = cookie_path.read_text(encoding="utf-8").strip()
    for item in content.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def save_cookie_file(
    cookies: Mapping[str, str], path: str | Path = gc.COOKIES_PATH
) -> None:
    """Persist cookies in the CLI's semicolon-delimited format."""

    cookie_path = Path(path)
    content = ";".join(f"{key}={value}" for key, value in cookies.items())
    cookie_path.write_text(content, encoding="utf-8")


def save_cookies(session: requests.Session) -> None:
    """Backward-compatible adapter for callers that hold a Session."""

    save_cookie_file(session.cookies.get_dict())


def use_cookies() -> dict[str, str]:
    """Backward-compatible alias for loading the default cookie file."""

    return load_cookie_file()
