"""Shared fixtures for the web application test suite."""

from pathlib import Path

import pytest

from webapp import create_app


@pytest.fixture
def app(tmp_path: Path):
    """Create an isolated test application backed by ``tmp_path``."""

    return create_app({"TESTING": True, "DATA_DIR": tmp_path})


@pytest.fixture
def client(app):
    """Return a test client for the isolated application."""

    return app.test_client()
