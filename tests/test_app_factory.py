from pathlib import Path

from webapp import create_app


def test_health_uses_standard_envelope(tmp_path: Path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "status": True,
        "data": {"service": "chaoxing-web"},
    }


def test_unknown_api_route_returns_json(tmp_path: Path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    response = app.test_client().get("/api/not-real")
    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"
