from __future__ import annotations

import base64
import os
import socket

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


def test_startup_fails_without_env_vars(seeded_state, daemon, tmp_path) -> None:
    from src.web.app import create_app
    os.environ.pop("MONITOR_USERNAME", None)
    os.environ.pop("MONITOR_PASSWORD", None)
    with pytest.raises(RuntimeError, match="MONITOR_USERNAME"):
        create_app(state=seeded_state, daemon=daemon, retention_days=30, screenshots_dir=str(tmp_path))


def test_startup_fails_without_secret_key(seeded_state, daemon, tmp_path, monkeypatch) -> None:
    from src.web.app import create_app
    monkeypatch.setenv("MONITOR_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("MONITOR_PASSWORD", TEST_PASSWORD)
    monkeypatch.delenv("MONITOR_SECRET_KEY")
    with pytest.raises(RuntimeError, match="MONITOR_SECRET_KEY"):
        create_app(state=seeded_state, daemon=daemon, retention_days=30, screenshots_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Targets managed from the web UI
# ---------------------------------------------------------------------------


def _listening_port() -> socket.socket:
    """Bound + listening socket; the kernel completes TCP handshakes without accept()."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PING_BODY = {"type": "ping", "name": "ui-host", "host": "10.0.0.1"}


def test_targets_page_has_no_auto_refresh(web_client: TestClient) -> None:
    response = web_client.get("/targets")
    assert response.status_code == 200
    assert 'http-equiv="refresh"' not in response.text


def test_add_target(web_client: TestClient, daemon) -> None:
    response = web_client.post("/api/targets", json=PING_BODY)
    assert response.status_code == 201
    assert daemon.has_target("ui-host")
    assert "ui-host" in web_client.get("/targets").text


def test_add_target_with_yaml_name_conflicts(web_client: TestClient) -> None:
    response = web_client.post("/api/targets", json={**PING_BODY, "name": "loopback"})
    assert response.status_code == 409


def test_add_target_twice_conflicts(web_client: TestClient) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    response = web_client.post("/api/targets", json=PING_BODY)
    assert response.status_code == 409


def test_add_tcp_target_invalid_port_422(web_client: TestClient) -> None:
    body = {"type": "tcp", "name": "bad", "host": "10.0.0.1", "port": 70000}
    response = web_client.post("/api/targets", json=body)
    assert response.status_code == 422


def test_delete_target(web_client: TestClient, daemon) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    response = web_client.delete("/api/targets/ui-host")
    assert response.status_code == 204
    assert not daemon.has_target("ui-host")
    assert web_client.delete("/api/targets/ui-host").status_code == 404


def test_delete_yaml_target_404(web_client: TestClient, daemon) -> None:
    response = web_client.delete("/api/targets/loopback")
    assert response.status_code == 404
    assert daemon.has_target("loopback")


def test_test_endpoint_tcp_up(web_client: TestClient, seeded_state) -> None:
    sock = _listening_port()
    try:
        body = {"type": "tcp", "name": "t", "host": "127.0.0.1", "port": sock.getsockname()[1]}
        response = web_client.post("/api/targets/test", json=body)
    finally:
        sock.close()
    assert response.json()["status"] == "UP"
    assert seeded_state.get_status("t") is None


def test_test_endpoint_tcp_down(web_client: TestClient) -> None:
    body = {"type": "tcp", "name": "t", "host": "127.0.0.1", "port": _closed_port(), "timeout": 1000}
    response = web_client.post("/api/targets/test", json=body)
    assert response.json()["status"] == "DOWN"
    assert response.json()["error_message"] == "Connection refused"


@pytest.mark.parametrize(
    "method,path",
    [("post", "/api/targets"), ("post", "/api/targets/test"), ("delete", "/api/targets/x"), ("get", "/targets")],
)
def test_target_routes_require_auth(unauthenticated_client: TestClient, method: str, path: str) -> None:
    response = unauthenticated_client.request(method, path, json=PING_BODY)
    assert response.status_code == 401


def test_add_http_target(web_client: TestClient, daemon) -> None:
    body = {"type": "http", "name": "site", "url": "https://site.example.com", "expected_text": "Welcome"}
    response = web_client.post("/api/targets", json=body)
    assert response.status_code == 201
    assert daemon.has_target("site")
    assert "https://site.example.com" in web_client.get("/targets").text


def test_add_http_target_without_scheme_422(web_client: TestClient) -> None:
    body = {"type": "http", "name": "site", "url": "site.example.com"}
    assert web_client.post("/api/targets", json=body).status_code == 422


CHANNEL_BODY = {"type": "telegram", "name": "ops", "bot_token": "123:SECRET", "chat_ids": ["42"]}


def test_channels_page_has_no_auto_refresh(web_client: TestClient) -> None:
    response = web_client.get("/channels")
    assert response.status_code == 200
    assert 'http-equiv="refresh"' not in response.text


def test_add_channel(web_client: TestClient, daemon) -> None:
    response = web_client.post("/api/channels", json=CHANNEL_BODY)
    assert response.status_code == 201
    assert daemon.has_channel("ops")
    page = web_client.get("/channels").text
    assert "ops" in page
    assert "SECRET" not in page


def test_add_channel_twice_conflicts(web_client: TestClient) -> None:
    web_client.post("/api/channels", json=CHANNEL_BODY)
    assert web_client.post("/api/channels", json=CHANNEL_BODY).status_code == 409


def test_add_channel_without_chat_ids_422(web_client: TestClient) -> None:
    response = web_client.post("/api/channels", json={**CHANNEL_BODY, "chat_ids": []})
    assert response.status_code == 422


def test_delete_channel(web_client: TestClient, daemon) -> None:
    web_client.post("/api/channels", json=CHANNEL_BODY)
    assert web_client.delete("/api/channels/ops").status_code == 204
    assert not daemon.has_channel("ops")
    assert web_client.delete("/api/channels/ops").status_code == 404


def test_channel_token_is_encrypted_at_rest(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/channels", json=CHANNEL_BODY)
    raw = seeded_state._conn.execute("SELECT config FROM alert_channels").fetchone()["config"]
    assert "SECRET" not in raw
    assert seeded_state.get_channels()[0]["bot_token"] == "123:SECRET"


@pytest.mark.parametrize("method,path", [("get", "/channels"), ("post", "/api/channels"), ("delete", "/api/channels/x")])
def test_channel_routes_require_auth(unauthenticated_client: TestClient, method: str, path: str) -> None:
    assert getattr(unauthenticated_client, method)(path).status_code == 401
