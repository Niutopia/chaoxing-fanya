"""Static contracts for the local Docker deployment and release hygiene."""

import re
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


def test_dockerignore_covers_coverage_sidecar_files_at_any_depth():
    dockerignore = {
        line.strip()
        for line in Path(".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".coverage.*" in dockerignore
    assert "**/.coverage.*" in dockerignore


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


def test_readme_describes_data_init_as_a_compose_up_dependency():
    readme = Path("README.md").read_text()
    assert "每次启动前" not in readme
    assert "docker compose up" in readme
    assert "docker compose restart" in readme
    assert "docker compose start" in readme
    assert "docker restart" in readme
    assert "docker compose run --rm data-init" in readme
    assert "不会主动重新运行" in readme or "不会重跑" in readme


def test_readme_validates_restore_archive_before_clearing_the_volume():
    readme = Path("README.md").read_text()
    restore_start = readme.index("恢复时")
    restore_end = readme.index("备份文件应放在", restore_start)
    restore = readme[restore_start:restore_end]

    assert "gzip -t" in restore
    assert "tar -tzf" in restore
    assert restore.index("gzip -t") < restore.index("tar -tzf")
    assert restore.index("tar -tzf") < restore.index("find /target")


def test_windows_build_scripts_use_declared_python_and_locked_frontend_install():
    scripts = [
        Path("build_portable.bat").read_text(),
        Path("clean_and_build_portable.bat").read_text(),
        Path("quick_build.bat").read_text(),
    ]
    portable_scripts = scripts[:2]

    for script in portable_scripts:
        version = re.search(r'set "PYTHON_VERSION=([^"\r\n]+)"', script)
        assert version is not None
        assert version.group(1).startswith("3.13.")
        assert "python313._pth" in script
        assert "python313.zip" in script
        assert "python311" not in script

    for script in scripts:
        assert "npm install" not in script
        assert "npm ci" in script


def test_portable_builds_copy_only_credential_free_configuration_templates():
    build = Path("build_portable.bat").read_text()
    clean_build = Path("clean_and_build_portable.bat").read_text()

    for script in (build, clean_build):
        assert "config.ini.example" in script
        assert 'copy "%SCRIPT_DIR%config.ini"' not in script
        assert 'copy "%SCRIPT_DIR%web_config.json"' not in script
        assert "不会打包 web_config.json" in script


def _restore_section(readme: str) -> str:
    start = readme.index("恢复时")
    end = readme.index("备份文件应放在", start)
    return readme[start:end]


def test_restore_stages_and_validates_archive_before_stopping_web_and_replacing_data():
    readme = Path("README.md").read_text()
    restore = _restore_section(readme)

    # The archive must be expanded into an isolated temporary directory first;
    # validation must happen before any service/volume mutation.
    extract = restore.index("tar -xzf")
    required_db = restore.index("chaoxing-web.sqlite3", extract)
    required_key = restore.index("secret.key", extract)
    stop = restore.index("docker compose stop web")
    safety_backup = restore.index("pre-restore")
    replace = restore.index("find /target")

    assert "mktemp -d" in restore
    assert "tar -tzf" in restore or "gzip -t" in restore
    assert extract < required_db < stop
    assert extract < required_key < stop
    assert stop < safety_backup < replace
    assert 'rm -rf -- "$safety_dir"' not in restore
    assert "chaoxing-data-pre-restore.tgz" in restore
    assert "docker compose down -v" not in restore
    assert "docker compose down -v" not in readme


def test_restore_replaces_data_only_from_the_verified_staging_directory():
    readme = Path("README.md").read_text()
    restore = _restore_section(readme)

    replace = restore[restore.index("find /target") :]
    assert "tar -xzf /backup.tgz" not in replace
    assert "/restore:ro" in restore
    assert "cp -a /restore/. /target/" in replace or "cp -R /restore/. /target/" in replace


def test_data_init_docs_describe_explicit_run_without_remove_precondition():
    readme = Path("README.md").read_text()
    lifecycle = readme[readme.index("`data-init`") : readme.index("备份 SQLite", readme.index("`data-init`"))]

    assert "docker compose run --rm data-init" in lifecycle
    assert "显式" in lifecycle
    assert "restart" in lifecycle and ("不会" in lifecycle or "不會" in lifecycle)
    assert "rm -f data-init" not in lifecycle


def test_portable_scripts_have_strict_reproducible_dependency_policy():
    scripts = {
        name: Path(name).read_text()
        for name in ("build_portable.bat", "clean_and_build_portable.bat")
    }

    for name, script in scripts.items():
        lowered = script.lower()
        # A portable artifact cannot silently fetch a moving bootstrap script or
        # install OCR packages from an unpinned index. OCR is explicitly absent.
        assert "get-pip.py" not in lowered
        assert "https://bootstrap.pypa.io/get-pip.py" not in lowered
        assert not re.search(r"pip\s+install[^\r\n]*\bpaddlepaddle\b", lowered)
        assert not re.search(r"pip\s+install[^\r\n]*\bpaddlex(?:\[|\s|\"|')", lowered)
        assert "便携版不包含本地 ocr" in lowered or "不包含 paddleocr" in lowered
        assert re.search(r'"%pip_bootstrap%"\s+install[^\r\n]*requirements\.txt', lowered)


def test_every_portable_build_runs_npm_ci_before_frontend_build_and_fails_fast():
    for name in ("build_portable.bat", "clean_and_build_portable.bat", "quick_build.bat"):
        lines = Path(name).read_text().splitlines()
        lowered = [line.lower() for line in lines]
        ci_indexes = [idx for idx, line in enumerate(lowered) if re.search(r"\bcall\s+npm\s+ci\b", line)]
        build_indexes = [idx for idx, line in enumerate(lowered) if re.search(r"\bcall\s+npm\s+run\s+build\b", line)]

        assert len(ci_indexes) == 1, name
        assert len(build_indexes) == 1, name
        ci_index = ci_indexes[0]
        build_index = build_indexes[0]
        assert ci_index < build_index, name
        assert not any(
            re.search(r"\bif\s+(?:not\s+)?exist\s+[\"']?[^\r\n]*\\?node_modules[\"']?\s*\(", line)
            for line in lowered
        )

        ci_failure_window = "\n".join(lowered[ci_index : ci_index + 12])
        assert re.search(r"if\s+errorlevel\s+1[\s\S]*?exit\s+/b\s+1", ci_failure_window)

        # A pre-existing dist/node_modules state must not provide a bypass around
        # the clean, lockfile-driven install.
        assert not any(
            "goto :frontend_done" in line
            for line in lowered[:ci_index]
        ), name


def test_compose_and_docs_keep_local_web_port_and_container_answer_host_contract():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    web = compose["services"]["web"]
    assert web["ports"] == ["127.0.0.1:5001:5000"]
    assert "host.docker.internal:host-gateway" in web["extra_hosts"]

    readme = Path("README.md").read_text()
    assert "Host answer API:" in readme
    assert "Container target:  http://host.docker.internal:8849/v1" in readme
    assert "127.0.0.1:5001" in readme
    assert "127.0.0.1:5001:5000" in readme or "127.0.0.1:5001" in readme


def test_portable_scripts_use_a_fixed_pip_zipapp_without_ensurepip_or_floating_bootstrap():
    for name in ("build_portable.bat", "clean_and_build_portable.bat"):
        script = Path(name).read_text()
        lowered = script.lower()

        assert "ensurepip" not in lowered
        assert "get-pip" not in lowered
        version = re.search(r'set "PIP_BOOTSTRAP_VERSION=([^"\r\n]+)"', script)
        assert version is not None
        assert re.fullmatch(r"\d+\.\d+\.\d+", version.group(1))
        assert "https://bootstrap.pypa.io/pip/zipapp/pip-%PIP_BOOTSTRAP_VERSION%.pyz" in script
        assert 'set "PIP_BOOTSTRAP=%BUILD_DIR%\\pip.pyz"' in script

        download = re.search(
            r"Invoke-WebRequest[^\r\n]*PIP_BOOTSTRAP_URL[^\r\n]*PIP_BOOTSTRAP",
            script,
            re.IGNORECASE,
        )
        assert download is not None
        install = re.search(
            r'"%PYTHON_EXE%"\s+"%PIP_BOOTSTRAP%"\s+install[^\r\n]*-r\s+"%SCRIPT_DIR%requirements\.txt"',
            script,
            re.IGNORECASE,
        )
        assert install is not None
        assert "-m pip" not in lowered

        download_index = script.index("Invoke-WebRequest")
        download_failure = script.index("if errorlevel 1", download_index)
        install_index = script.index('"%PYTHON_EXE%" "%PIP_BOOTSTRAP%" install')
        assert download_index < download_failure < install_index
        assert re.search(
            r"if errorlevel 1[\s\S]*?exit /b 1",
            script[download_failure : install_index + 1],
        )


def test_clean_build_rejects_local_embed_without_python313_runtime_marker():
    script = Path("clean_and_build_portable.bat").read_text()
    pth_assignment = 'set "PTH_FILE=%PYTHON_DIR%\\python313._pth"'
    pth_index = script.index(pth_assignment)
    guard = script.index('if not exist "%PTH_FILE%" (', pth_index)
    guard_end = script.index(")", guard)

    assert "python311" not in script.lower()
    assert guard > pth_index
    assert "exit /b 1" in script[guard:guard_end]
    assert "pause" not in script[guard:guard_end].lower()
    assert script.index("echo python313.zip", guard_end) > guard_end
    assert 'if exist "%PTH_FILE%" (' not in script


def test_restore_has_explicit_failure_rollback_after_second_backup():
    readme = Path("README.md").read_text()
    restore = _restore_section(readme)
    safety = restore.index("chaoxing-data-pre-restore.tgz")
    replace = restore.index(
        'if docker run --rm \\\n'
        '  -v "${data_volume}:/target"',
        safety,
    )
    rollback_definition = restore.index("rollback_original()")

    assert "set -euo pipefail" not in restore
    assert "set -e" not in restore
    assert "set -uo pipefail" in restore or "set -u -o pipefail" in restore
    assert "fail_after_backup" in restore
    assert restore.count("fail_after_backup") >= 3
    assert safety < rollback_definition < replace

    rollback = restore[rollback_definition:replace]
    assert rollback.index("docker compose stop web") < rollback.index("tar -xzf /backup/chaoxing-data-pre-restore.tgz")
    assert rollback.index("tar -xzf /backup/chaoxing-data-pre-restore.tgz") < rollback.index("docker compose run --rm data-init")
    assert rollback.index("docker compose run --rm data-init") < rollback.index("docker compose up -d web")
    assert "/backup:ro" in rollback

    post_backup = restore[safety:]
    assert "docker compose run --rm data-init" in post_backup
    assert "docker compose up -d web" in post_backup
    assert "/api/health" in post_backup
    assert "curl -fsS" in post_backup
    assert "docker compose down -v" not in restore


def test_data_init_docs_distinguish_restarting_data_init_from_restarting_web():
    readme = Path("README.md").read_text()
    lifecycle = readme[readme.index("`data-init`") : readme.index("备份 SQLite", readme.index("`data-init`"))]

    assert "docker compose restart data-init" in lifecycle
    assert "docker compose restart web" in lifecycle
    assert "docker restart <web>" in lifecycle
    assert "docker compose start web" in lifecycle
    assert "不会主动重新运行" in lifecycle or "不会重新运行" in lifecycle
    assert "docker compose run --rm data-init" in lifecycle
    assert "docker compose rm -f data-init" not in lifecycle
