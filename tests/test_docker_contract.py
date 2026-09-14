"""Static contracts for the local Docker deployment."""

from pathlib import Path

import yaml


def test_compose_binds_only_localhost_and_persists_data():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    web = compose["services"]["web"]
    assert web["ports"] == ["127.0.0.1:5001:5000"]
    assert "chaoxing-data:/app/data" in web["volumes"]
    assert "host.docker.internal:host-gateway" in web["extra_hosts"]
    assert web["restart"] == "unless-stopped"
    assert web["healthcheck"]["test"][:3] == ["CMD", "python", "-c"]
    assert "/api/health" in web["healthcheck"]["test"][3]
    assert "chaoxing-data" in compose["volumes"]


def test_dockerfile_builds_frontend_and_runs_one_gunicorn_worker():
    dockerfile = Path("Dockerfile").read_text()
    assert "npm run build" in dockerfile
    assert "COPY --from=web-builder" in dockerfile
    assert "EXPOSE 5000" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--threads", "8"' in dockerfile
    assert '"--bind", "0.0.0.0:5000"' in dockerfile
    assert 'VOLUME ["/app/data"]' in dockerfile


def test_app_factory_enables_loopback_translation_inside_docker(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CHAOXING_RUNNING_IN_DOCKER", "1")

    from webapp import create_app

    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    service = app.extensions["services"]["answer_connection_service"]
    runner = app.extensions["services"]["task_manager"].runner

    assert service.running_in_docker is True
    assert runner.running_in_docker is True


def test_dockerignore_excludes_data_backups_and_readme_uses_external_backup_path():
    dockerignore = {
        line.strip()
        for line in Path(".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "chaoxing-data-backup.tgz" in dockerignore
    assert "*.tgz" in dockerignore
    assert "*.tar.gz" in dockerignore

    readme = Path("README.md").read_text()
    assert '-v "$PWD":/backup' not in readme
    assert "mktemp" in readme


def test_env_example_does_not_advertise_unwired_account_limit():
    env_example = Path(".env.example").read_text()
    assert "CHAOXING_MAX_ACTIVE_ACCOUNTS" not in env_example


def test_readme_uses_shell_safe_ocr_placeholder_and_current_docker_troubleshooting():
    readme = Path("README.md").read_text()
    assert 'export CHAOXING_VISION_OCR_KEY="YOUR_VISION_KEY"' in readme
    assert "export CHAOXING_VISION_OCR_KEY=<" not in readme
    assert "netstat -ano | findstr :5000" not in readme
    assert "findstr :3000" not in readme
    assert "/config/config.ini" not in readme
    assert "127.0.0.1:5001" in readme
    assert "/app/data" in readme
    assert "Settings" in readme


def test_docker_contract_keeps_runtime_environment_explicit_and_excludes_portables():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    environment = compose["services"]["web"]["environment"]
    if isinstance(environment, list):
        environment = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in environment}
    assert environment["CHAOXING_DATA_DIR"] == "/app/data"
    assert environment["CHAOXING_RUNNING_IN_DOCKER"] == "1"

    dockerignore = Path(".dockerignore").read_text()
    assert "*.zip" in dockerignore
    assert "**/*.zip" in dockerignore
    assert "portable/" in dockerignore
    assert "chaoxing_portable/" in dockerignore
    assert "portable_build/" in dockerignore


def test_readme_backup_resolves_the_running_compose_volume_and_cli_docs_are_precise():
    readme = Path("README.md").read_text()
    assert "docker compose ps -q web" in readme
    assert "docker inspect" in readme
    assert "Destination" in readme and "/app/data" in readme
    assert "chaoxing-fanya_chaoxing-data" not in readme
    assert "python main.py" in readme and "交互式运行" in readme
    assert "npm ci" in readme
    assert "删除 node_modules 与锁文件" not in readme
