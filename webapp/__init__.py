import os
import threading
import weakref
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from .account_service import AccountService
from .answer_connection import AnswerConnectionService
from .crypto import SecretBox
from .routes.accounts import NoopTaskGuard, accounts
from .routes.settings import settings
from .routes.tasks import tasks
from .store import SQLiteStore
from .task_logging import install_task_log_sink, unregister_task_log_sink
from .task_manager import TaskManager
from .study_runner import ChaoxingStudyRunner


def _default_chaoxing_factory(*, auth, cookie_update_callback):
    """Build a Chaoxing client with an HTTP session owned by one account."""

    from api.base import Account, Chaoxing, build_session

    return Chaoxing(
        account=Account(auth.username, auth.password),
        session=build_session(auth.cookies),
        cookie_update_callback=cookie_update_callback,
    )


def _default_cookie_update_callback_factory(store):
    """Build account-scoped cookie callbacks for study tasks."""

    def factory(account_id: str):
        return lambda cookies: store.save_cookies(str(account_id), cookies)

    return factory


def _default_task_runner(_context):
    """Backward-compatible no-op hook for callers that imported the symbol.

    ``create_app`` now composes :class:`ChaoxingStudyRunner` by default; this
    legacy function remains available for explicit test/custom integrations
    that still select it as ``TASK_RUNNER``.
    """

    return None


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    default_static_dir = Path(__file__).resolve().parent.parent / "web" / "dist"
    configured_data_dir = os.environ.get("CHAOXING_DATA_DIR")
    data_dir = Path(configured_data_dir or "data").expanduser().resolve()
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE_PATH=data_dir / "chaoxing-web.sqlite3",
        RUNNING_IN_DOCKER=os.environ.get("CHAOXING_RUNNING_IN_DOCKER", False),
        STATIC_DIR=default_static_dir,
        MAX_CONTENT_LENGTH=1024 * 1024,
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
    answer_connection_service = app.config.get("ANSWER_CONNECTION_SERVICE")
    if answer_connection_service is None:
        answer_connection_service = app.config.get("ANSWER_SERVICE")
    if answer_connection_service is None:
        docker_value = app.config.get("RUNNING_IN_DOCKER", False)
        running_in_docker = (
            docker_value
            if isinstance(docker_value, bool)
            else str(docker_value).strip().lower()
            in {"1", "true", "yes", "y", "on"}
        )
        answer_connection_service = AnswerConnectionService(
            store,
            transport=app.config.get(
                "ANSWER_CONNECTION_TRANSPORT", app.config.get("ANSWER_TRANSPORT")
            ),
            client=app.config.get("ANSWER_CONNECTION_CLIENT"),
            http_client=app.config.get("ANSWER_CONNECTION_HTTP_CLIENT"),
            httpx_client=app.config.get("ANSWER_CONNECTION_HTTPX_CLIENT"),
            client_factory=app.config.get("ANSWER_CONNECTION_CLIENT_FACTORY"),
            running_in_docker=running_in_docker,
        )

    task_manager = app.config.get("TASK_MANAGER")
    if task_manager is None:
        configured_limit = app.config.get("MAX_ACTIVE_ACCOUNTS")
        if configured_limit is None:
            configured_limit = store.get_runtime_settings().max_active_accounts
        runner = app.config.get("TASK_RUNNER")
        if runner is None:
            runner_docker_value = getattr(
                answer_connection_service,
                "running_in_docker",
                app.config.get("RUNNING_IN_DOCKER", False),
            )
            runner_running_in_docker = (
                runner_docker_value
                if isinstance(runner_docker_value, bool)
                else str(runner_docker_value).strip().lower()
                in {"1", "true", "yes", "y", "on"}
            )
            runner = ChaoxingStudyRunner(
                data_dir=app.config["DATA_DIR"],
                engine_factory=app.config.get("CHAOXING_ENGINE_FACTORY"),
                cookie_update_callback_factory=_default_cookie_update_callback_factory(
                    store
                ),
                running_in_docker=runner_running_in_docker,
            )
        answer_semaphore = app.config.get("ANSWER_SEMAPHORE")
        if answer_semaphore is None:
            get_semaphore = getattr(answer_connection_service, "get_semaphore", None)
            if callable(get_semaphore):
                answer_semaphore = get_semaphore()
        task_manager = TaskManager(
            runner=runner,
            max_active_accounts=configured_limit,
            answer_semaphore=answer_semaphore,
        )

    # The account routes only require the active-task guard protocol.  Keep an
    # explicitly injected guard for compatibility with route tests and custom
    # integrations; otherwise the real process-local manager owns admission.
    task_guard = app.config.get("TASK_GUARD") or task_manager or NoopTaskGuard()
    coordination_lock = getattr(task_manager, "lock", None)
    if coordination_lock is None:
        coordination_lock = app.config.get("ACCOUNT_TASK_LOCK")
    if coordination_lock is None:
        coordination_lock = threading.RLock()
    task_log_sink_id = install_task_log_sink(task_manager)
    app.extensions["services"] = {
        "secret_box": secret_box,
        "store": store,
        "account_service": account_service,
        "answer_connection_service": answer_connection_service,
        # Keep a concise alias for callers that used the service name before
        # the application factory settled on its canonical extension key.
        "answer_connection": answer_connection_service,
        "answer_service": answer_connection_service,
        "task_guard": task_guard,
        "task_manager": task_manager,
        "account_task_lock": coordination_lock,
    }
    app.extensions["task_manager"] = task_manager
    app.extensions["account_task_lock"] = coordination_lock
    app.extensions["task_log_sink_id"] = task_log_sink_id
    # Keep the sink ID discoverable alongside other service extension values
    # for fixture teardown code that only inspects the services mapping.
    app.extensions["services"]["task_log_sink_id"] = task_log_sink_id
    app.extensions["services"]["_answer_connection_service_default"] = (
        answer_connection_service
    )
    # A process-global Loguru sink must outlive individual Flask app
    # contexts, but it should not retain managers from discarded app
    # instances forever.  The finalizer is weakly attached to this app and
    # unregisters exactly its manager; another live app keeps the sink alive.
    app.extensions["task_log_sink_finalizer"] = weakref.finalize(
        app,
        unregister_task_log_sink,
        task_manager,
    )
    app.register_blueprint(accounts)
    app.register_blueprint(settings)
    app.register_blueprint(tasks)

    @app.get("/api/health")
    def health():
        return jsonify(status=True, data={"service": "chaoxing-web"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(status=False, msg="Not Found", code="not_found"), 404

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(
            status=False,
            msg="Request body is too large",
            code="request_too_large",
        ), 413

    @app.errorhandler(Exception)
    def internal_error(error):
        """Keep unexpected API failures in the stable JSON envelope.

        Flask still logs the traceback server-side.  The response intentionally
        omits exception text because injected clients and storage adapters can
        include credentials or request bodies in it.
        """

        if isinstance(error, HTTPException):
            # Flask uses HTTPException for routing semantics (405) and for
            # deliberate client errors (400).  Preserve those statuses and
            # headers; only an actual unexpected exception is an internal 500.
            return error

        return jsonify(
            status=False,
            msg="Internal server error",
            code="internal_error",
        ), 500

    # Keep unknown API methods/paths in the JSON API namespace.  Without an
    # all-methods guard, the GET-only SPA fallback would turn an unknown
    # ``POST /api/...`` into a method-not-allowed response and make the
    # frontend catch-all appear to own the API.
    @app.route(
        "/api",
        defaults={"path": ""},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    @app.route(
        "/api/<path:path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    def api_not_found(path: str):
        return not_found(None)

    # Keep the API namespace outside the SPA catch-all.  This route is
    # deliberately registered after all API blueprints so an unknown API URL
    # still reaches the JSON 404 handler instead of returning index.html.
    static_dir = Path(app.config["STATIC_DIR"])
    if static_dir.is_dir():

        @app.get("/")
        def serve_index():
            return send_from_directory(static_dir, "index.html")

        @app.get("/<path:path>")
        def serve_static(path: str):
            if path == "api" or path.startswith("api/"):
                return not_found(None)
            candidate = static_dir / path
            if candidate.is_file():
                return send_from_directory(static_dir, path)
            return send_from_directory(static_dir, "index.html")

    return app
