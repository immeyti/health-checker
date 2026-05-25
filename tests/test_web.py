from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME, make_result


def test_root_200(web_client: TestClient) -> None:
    response = web_client.get("/")
    assert response.status_code == 200


def test_root_html(web_client: TestClient) -> None:
    response = web_client.get("/")
    assert "text/html" in response.headers["content-type"]


def test_root_contains_target(web_client: TestClient) -> None:
    response = web_client.get("/")
    assert "host-a" in response.text


def test_api_status_list(web_client: TestClient) -> None:
    response = web_client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1


def test_api_status_single(web_client: TestClient) -> None:
    response = web_client.get("/api/status/host-a")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "host-a"
    assert data["current_status"] == "UP"


def test_api_status_404(web_client: TestClient) -> None:
    response = web_client.get("/api/status/ghost")
    assert response.status_code == 404


def test_api_history_list(web_client: TestClient) -> None:
    response = web_client.get("/api/history/host-a")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert all(entry["target_name"] == "host-a" for entry in data)


def test_api_history_empty(web_client: TestClient) -> None:
    response = web_client.get("/api/history/ghost")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Auth boundary tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/status", "/api/status/host-a", "/api/history/host-a"])
def test_all_routes_require_auth(unauthenticated_client: TestClient, path: str) -> None:
    response = unauthenticated_client.get(path)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Basic"


def test_wrong_password_returns_401(unauthenticated_client: TestClient) -> None:
    bad_creds = base64.b64encode(f"{TEST_USERNAME}:wrongpass".encode()).decode()
    response = unauthenticated_client.get("/", headers={"Authorization": f"Basic {bad_creds}"})
    assert response.status_code == 401


def test_startup_fails_without_env_vars(seeded_state, tmp_path) -> None:
    from src.web.app import create_app
    os.environ.pop("MONITOR_USERNAME", None)
    os.environ.pop("MONITOR_PASSWORD", None)
    with pytest.raises(RuntimeError, match="MONITOR_USERNAME"):
        create_app(state=seeded_state, retention_days=30, screenshots_dir=str(tmp_path))
