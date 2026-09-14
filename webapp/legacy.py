"""Small compatibility endpoints for the historical module-level WSGI app.

The application factory intentionally exposes only the account/task API.  A
few older integrations still import :mod:`app` and call its login/course
helpers, so the standalone ``app.app`` entry point installs these two shims.
They are kept here (rather than in ``app.py``) so the WSGI module remains a
thin composition layer and the new factory has no legacy routes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from flask import Flask, jsonify, request


def register_legacy_routes(
    app: Flask,
    *,
    symbol: Callable[[str], Any],
) -> None:
    """Install only the historical login/course shims on ``app``.

    ``symbol`` is resolved at request time so existing tests and embedders can
    replace their Chaoxing/session collaborators after importing ``app``.
    The factory-created app never calls this function.
    """

    def build_client(username: str, password: str):
        Account = symbol("Account")
        Chaoxing = symbol("Chaoxing")
        Tiku = symbol("Tiku")
        account_cookie_path = symbol("account_cookie_path")
        load_cookie_file = symbol("load_cookie_file")
        build_session = symbol("build_session")
        save_cookie_file = symbol("save_cookie_file")

        account = Account(username, password)
        cookie_path = account_cookie_path(username)
        session = build_session(load_cookie_file(cookie_path))
        return Chaoxing(
            account=account,
            tiku=Tiku(),
            query_delay=0,
            session=session,
            cookie_update_callback=partial(save_cookie_file, path=cookie_path),
        )

    @app.post("/api/login")
    def legacy_login():
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            return jsonify(status=False, msg="用户名或密码不能为空"), 400
        username = payload.get("username")
        password = payload.get("password")
        use_cookies = payload.get("use_cookies", False)
        if not username or not password:
            return jsonify(status=False, msg="用户名或密码不能为空"), 400
        try:
            client = build_client(str(username), str(password))
            result = client.login(login_with_cookies=bool(use_cookies))
        except Exception:
            return jsonify(status=False, msg="登录失败"), 401
        if isinstance(result, Mapping) and result.get("status"):
            return jsonify(
                status=True,
                msg="登录成功",
                data={"username": str(username)},
            )
        return jsonify(status=False, msg="登录失败"), 401

    @app.post("/api/courses")
    def legacy_courses():
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            return jsonify(status=False, msg="登录失败"), 401
        username = payload.get("username")
        password = payload.get("password")
        use_cookies = payload.get("use_cookies", False)
        if not username or not password:
            return jsonify(status=False, msg="登录失败"), 401
        try:
            client = build_client(str(username), str(password))
            result = client.login(login_with_cookies=bool(use_cookies))
            if not isinstance(result, Mapping) or not result.get("status"):
                return jsonify(status=False, msg="登录失败"), 401
            return jsonify(status=True, data=client.get_course_list())
        except Exception:
            return jsonify(status=False, msg="获取课程列表失败"), 502


__all__ = ["register_legacy_routes"]
