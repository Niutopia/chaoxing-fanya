from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify

from .account_service import AccountService
from .answer_connection import AnswerConnectionService
from .crypto import SecretBox
from .routes.accounts import NoopTaskGuard, accounts
from .routes.settings import settings
from .store import SQLiteStore
from .task_logging import install_task_log_sink
from .task_manager import TaskManager


def _default_chaoxing_factory(*, auth, cookie_update_callback):
    """Build a Chaoxing client with an HTTP session owned by one account."""

    from api.base import Account, Chaoxing, build_session

    return Chaoxing(
        account=Account(auth.username, auth.password),
        session=build_session(auth.cookies),
        cookie_update_callback=cookie_update_callback,
    )


def _default_task_runner(_context):
    """No-op runner used until the study-engine adapter is composed.

    Task 6 owns lifecycle and isolation.  The actual Chaoxing study runner is
    injected later, while a no-op keeps the application factory usable in
    development and in tests that only exercise account/settings APIs.
    """

    return None


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
        runner = app.config.get("TASK_RUNNER", _default_task_runner)
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
    }
    app.extensions["task_manager"] = task_manager
    app.extensions["task_log_sink_id"] = task_log_sink_id
    # Keep the sink ID discoverable alongside other service extension values
    # for fixture teardown code that only inspects the services mapping.
    app.extensions["services"]["task_log_sink_id"] = task_log_sink_id
    app.extensions["services"]["_answer_connection_service_default"] = (
        answer_connection_service
    )
    app.register_blueprint(accounts)
    app.register_blueprint(settings)

    @app.get("/api/health")
    def health():
        return jsonify(status=True, data={"service": "chaoxing-web"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(status=False, msg="Not Found", code="not_found"), 404

    return app
