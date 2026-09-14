from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify


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

    @app.get("/api/health")
    def health():
        return jsonify(status=True, data={"service": "chaoxing-web"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(status=False, msg="Not Found", code="not_found"), 404

    return app
