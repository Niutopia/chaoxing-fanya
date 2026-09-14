from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify

from .account_service import AccountService
from .crypto import SecretBox
from .routes.accounts import NoopTaskGuard, accounts
from .store import SQLiteStore


def _default_chaoxing_factory(*, auth, cookie_update_callback):
    """Build a Chaoxing client with an HTTP session owned by one account."""

    from api.base import Account, Chaoxing, build_session

    return Chaoxing(
        account=Account(auth.username, auth.password),
        session=build_session(auth.cookies),
        cookie_update_callback=cookie_update_callback,
    )


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    data_dir = Path("data").resolve()
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE_PATH=data_dir / "chaoxing-web.sqlite3",
        RUNNING_IN_DOCKER=False,
    )
    if test_config:
        app.config.update(test_config)
    app.config["DATA_DIR"] = Path(app.config["DATA_DIR"])
    app.config["DATABASE_PATH"] = app.config["DATA_DIR"] / "chaoxing-web.sqlite3"
    app.config["DATA_DIR"].mkdir(parents=True, exist_ok=True)

    secret_box = SecretBox(app.config["DATA_DIR"])
    store = SQLiteStore(app.config["DATABASE_PATH"], secret_box)
    chaoxing_factory = app.config.get("CHAOXING_FACTORY", _default_chaoxing_factory)
    account_service = app.config.get("ACCOUNT_SERVICE")
    if account_service is None:
        account_service = AccountService(store, chaoxing_factory)
    task_guard = app.config.get("TASK_GUARD") or NoopTaskGuard()
    app.extensions["services"] = {
        "secret_box": secret_box,
        "store": store,
        "account_service": account_service,
        "task_guard": task_guard,
    }
    app.register_blueprint(accounts)

    @app.get("/api/health")
    def health():
        return jsonify(status=True, data={"service": "chaoxing-web"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(status=False, msg="Not Found", code="not_found"), 404

    return app
