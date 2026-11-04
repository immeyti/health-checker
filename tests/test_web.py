from __future__ import annotations

import base64
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from src.daemon import MonitoringDaemon
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


def test_attempt_csv_includes_failed_retry(web_client: TestClient, seeded_state) -> None:
    seeded_state.record_attempt(make_result(status="DOWN", error_message="HTTP 503"), "cycle-1", 1)
    seeded_state.record_attempt(make_result(status="UP"), "cycle-1", 2)
    response = web_client.get("/api/attempts/host-a.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "HTTP 503" in response.text
    assert "cycle-1" in response.text
    assert ",2,host-a,ping,UP," in response.text


def test_attempt_csv_requires_auth(unauthenticated_client: TestClient) -> None:
    assert unauthenticated_client.get("/api/attempts/host-a.csv").status_code == 401


def test_history_csv_exports_existing_results(web_client: TestClient) -> None:
    response = web_client.get("/api/history/host-a.csv")
    assert response.status_code == 200
    assert "checked_at_utc,target_name,target_type,final_status" in response.text
    assert ",host-a,ping,UP," in response.text


def test_history_csv_requires_auth(unauthenticated_client: TestClient) -> None:
    assert unauthenticated_client.get("/api/history/host-a.csv").status_code == 401


def test_history_page_shows_outcomes_and_retries(web_client: TestClient, seeded_state) -> None:
    seeded_state.update(make_result(status="DOWN", error_message="Connection timed out"))
    seeded_state.update(make_result(status="UP"))
    seeded_state.record_attempt(make_result(status="DOWN", error_message="ConnectTimeout"), "cycle-1", 1)
    seeded_state.record_attempt(make_result(status="UP"), "cycle-1", 2)
    response = web_client.get("/history/host-a")
    assert response.status_code == 200
    assert "Recorded outcomes" in response.text
    assert "Recent DOWN" in response.text
    assert "Connection timed out" in response.text
    assert "Recovered on retry" in response.text
    assert "ConnectTimeout" in response.text
    assert "/api/attempts/host-a.csv" in response.text


def test_history_page_requires_auth_and_handles_missing_target(unauthenticated_client: TestClient,
                                                              web_client: TestClient) -> None:
    assert unauthenticated_client.get("/history/host-a").status_code == 401
    assert web_client.get("/history/ghost").status_code == 404


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


def test_auth_disabled_without_env_vars(seeded_state, daemon, tmp_path, monkeypatch) -> None:
    from src.web.app import create_app
    monkeypatch.delenv("MONITOR_USERNAME", raising=False)
    monkeypatch.delenv("MONITOR_PASSWORD", raising=False)
    app = create_app(state=seeded_state, daemon=daemon, retention_days=30, screenshots_dir=str(tmp_path))
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 200


def test_startup_fails_with_only_one_credential(seeded_state, daemon, tmp_path, monkeypatch) -> None:
    from src.web.app import create_app
    monkeypatch.setenv("MONITOR_USERNAME", TEST_USERNAME)
    monkeypatch.delenv("MONITOR_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="MONITOR_PASSWORD"):
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


def test_add_channel_with_topic(web_client: TestClient, daemon, seeded_state) -> None:
    web_client.post("/api/channels", json={**CHANNEL_BODY, "topic_id": 586})
    assert seeded_state.get_channels()[0]["topic_id"] == 586
    assert "topic 586" in web_client.get("/channels").text
    reloaded = MonitoringDaemon(config=daemon._config, state=seeded_state)
    assert reloaded._ui_channels["ops"]._topic_id == 586



def test_attempt_csv_has_ips_network_flag_and_tehran_range(web_client: TestClient, seeded_state) -> None:
    inside = make_result(status="DOWN", checked_at=datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc))
    inside.diagnostics.update(source_ip="198.51.100.4", resolved_ips="203.0.113.7")
    seeded_state.record_attempt(inside, "in-window", 1)
    seeded_state.mark_network_issue(["in-window"], "monitor_offline")
    seeded_state.record_attempt(
        make_result(checked_at=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)), "after", 1)

    # 10:00–12:00 Tehran (UTC+3:30) is 06:30–08:30 UTC
    response = web_client.get("/api/attempts/host-a.csv",
                              params={"since": "2026-10-22T10:00", "until": "2026-10-22T12:00"})
    assert response.status_code == 200
    header, *rows = response.text.strip().splitlines()
    assert "network_issue" in header and "source_ip" in header and "resolved_ips" in header
    assert len(rows) == 1
    assert "in-window" in rows[0] and "monitor_offline" in rows[0]
    assert "198.51.100.4" in rows[0] and "203.0.113.7" in rows[0]
    assert "host-a-attempts.csv" in response.headers["content-disposition"]


def test_csv_export_rejects_reversed_range(web_client: TestClient) -> None:
    response = web_client.get("/api/history/host-a.csv",
                              params={"since": "2026-10-23T00:00", "until": "2026-10-22T00:00"})
    assert response.status_code == 422


def test_history_page_has_range_form(web_client: TestClient) -> None:
    response = web_client.get("/history/host-a")
    assert 'type="datetime-local" name="since"' in response.text
    assert 'formaction="/api/history/host-a.csv"' in response.text


def test_csv_export_treats_blank_form_fields_as_no_bound(web_client: TestClient) -> None:
    for path in ("/api/history/host-a.csv", "/api/attempts/host-a.csv"):
        response = web_client.get(f"{path}?since=&until=")
        assert response.status_code == 200, path


def test_csv_export_rejects_malformed_time(web_client: TestClient) -> None:
    response = web_client.get("/api/attempts/host-a.csv", params={"since": "yesterday"})
    assert response.status_code == 422
    assert "since must be a date and time" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Editing and stopping targets
# ---------------------------------------------------------------------------


DASHBOARD_BODY = {
    "type": "dashboard", "name": "panel", "url": "https://panel.example.com/login",
    "username": "admin", "password": "s3cret-value",
    "indicator_type": "url_contains", "indicator_value": "/home",
}


def test_edit_target_applies_settings(web_client: TestClient, seeded_state, daemon) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    body = {**PING_BODY, "host": "10.0.0.9", "timeout": 1000, "max_latency": 250}
    response = web_client.put("/api/targets/ui-host", json=body)
    assert response.status_code == 200
    saved = seeded_state.get_target("ui-host")
    assert (saved.host, saved.timeout, saved.max_latency) == ("10.0.0.9", 1000, 250)
    assert daemon.has_target("ui-host")
    assert "10.0.0.9" in web_client.get("/targets").text


def test_edit_target_rename_keeps_history(web_client: TestClient, seeded_state, daemon) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    seeded_state.update(make_result(name="ui-host", status="UP"))
    response = web_client.put("/api/targets/ui-host", json={**PING_BODY, "name": "renamed"})
    assert response.status_code == 200
    assert daemon.has_target("renamed") and not daemon.has_target("ui-host")
    assert len(web_client.get("/api/history/renamed").json()) == 1
    assert web_client.get("/api/status/ui-host").status_code == 404


def test_edit_unknown_or_yaml_target_404(web_client: TestClient) -> None:
    assert web_client.put("/api/targets/nope", json=PING_BODY).status_code == 404
    assert web_client.put("/api/targets/loopback", json={**PING_BODY, "name": "loopback"}).status_code == 404


def test_edit_target_rename_to_existing_conflicts(web_client: TestClient) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    response = web_client.put("/api/targets/ui-host", json={**PING_BODY, "name": "loopback"})
    assert response.status_code == 409


def test_edit_target_cannot_change_type(web_client: TestClient) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    body = {"type": "tcp", "name": "ui-host", "host": "10.0.0.1", "port": 443}
    assert web_client.put("/api/targets/ui-host", json=body).status_code == 422


def test_edit_dashboard_blank_password_keeps_current(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/targets", json=DASHBOARD_BODY)
    body = {**DASHBOARD_BODY, "password": "", "username": "root"}
    assert web_client.put("/api/targets/panel", json=body).status_code == 200
    saved = seeded_state.get_target("panel")
    assert (saved.username, saved.password) == ("root", "s3cret-value")


def test_edit_dashboard_new_password_replaces_current(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/targets", json=DASHBOARD_BODY)
    assert web_client.put("/api/targets/panel", json={**DASHBOARD_BODY, "password": "changed"}).status_code == 200
    assert seeded_state.get_target("panel").password == "changed"


def test_edit_dashboard_new_url_requires_password(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/targets", json=DASHBOARD_BODY)
    body = {**DASHBOARD_BODY, "password": "", "url": "https://elsewhere.example.com/login"}
    assert web_client.put("/api/targets/panel", json=body).status_code == 422
    assert web_client.post("/api/targets/test?editing=panel", json=body).status_code == 422
    assert seeded_state.get_target("panel").url == DASHBOARD_BODY["url"]


def test_add_dashboard_without_password_422(web_client: TestClient) -> None:
    body = {**DASHBOARD_BODY, "password": ""}
    assert web_client.post("/api/targets", json=body).status_code == 422
    assert web_client.post("/api/targets/test", json=body).status_code == 422


def test_get_target_never_returns_password(web_client: TestClient) -> None:
    web_client.post("/api/targets", json=DASHBOARD_BODY)
    response = web_client.get("/api/targets/panel")
    assert response.status_code == 200
    assert "s3cret-value" not in response.text
    values = response.json()
    assert "password" not in values
    assert values["type"] == "dashboard"
    assert values["indicator_type"] == "url_contains"
    assert values["indicator_value"] == "/home"
    assert values["paused"] is False


def test_get_yaml_target_404(web_client: TestClient) -> None:
    assert web_client.get("/api/targets/loopback").status_code == 404


def test_pause_and_resume_target(web_client: TestClient, seeded_state, daemon) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    assert web_client.put("/api/targets/ui-host/pause").status_code == 204
    assert seeded_state.get_paused_targets() == {"ui-host"}
    assert daemon._paused == {"ui-host"}
    assert web_client.get("/api/targets/ui-host").json()["paused"] is True
    assert "Resume" in web_client.get("/targets").text

    assert web_client.put("/api/targets/ui-host/resume").status_code == 204
    assert seeded_state.get_paused_targets() == set()
    assert daemon._paused == set()


def test_pause_unknown_or_yaml_target_404(web_client: TestClient, daemon) -> None:
    assert web_client.put("/api/targets/nope/pause").status_code == 404
    assert web_client.put("/api/targets/loopback/pause").status_code == 404
    assert web_client.put("/api/targets/loopback/resume").status_code == 404
    assert daemon._paused == set()


def test_stopped_target_is_badged_and_left_out_of_counts(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    seeded_state.update(make_result(name="ui-host", status="DOWN"))
    assert 'text-red-400">1<' in web_client.get("/").text.replace("\n", "")

    web_client.put("/api/targets/ui-host/pause")

    page = web_client.get("/").text
    assert "Stopped" in page
    assert 'text-red-400">1<' not in page.replace("\n", "")
    assert "STOPPED" in web_client.get("/history/ui-host").text
    assert web_client.get("/api/status/ui-host").json()["paused"] is True


def test_blank_max_latency_means_no_limit(web_client: TestClient, seeded_state) -> None:
    response = web_client.post("/api/targets", json={**PING_BODY, "max_latency": ""})
    assert response.status_code == 201
    assert seeded_state.get_target("ui-host").max_latency is None


@pytest.mark.parametrize("value", [0, -5, "abc"])
def test_invalid_max_latency_422(web_client: TestClient, value) -> None:
    assert web_client.post("/api/targets", json={**PING_BODY, "max_latency": value}).status_code == 422


@pytest.mark.parametrize(
    "method,path",
    [("get", "/api/targets/x"), ("put", "/api/targets/x"), ("put", "/api/targets/x/pause"), ("put", "/api/targets/x/resume")],
)
def test_edit_and_stop_routes_require_auth(unauthenticated_client: TestClient, method: str, path: str) -> None:
    response = unauthenticated_client.request(method, path, json=PING_BODY)
    assert response.status_code == 401


def test_stop_rejects_post_so_other_sites_cannot_trigger_it(web_client: TestClient, seeded_state) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    assert web_client.post("/api/targets/ui-host/pause").status_code == 405
    assert seeded_state.get_paused_targets() == set()


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded"])
def test_add_target_rejects_cross_site_content_types(web_client: TestClient, daemon, ctype: str) -> None:
    response = web_client.post(
        "/api/targets", content=json.dumps(PING_BODY), headers={"Content-Type": ctype}
    )
    assert response.status_code == 422
    assert not daemon.has_target("ui-host")


@pytest.mark.parametrize("name", ["a/b", "/", "   "])
def test_unaddressable_target_names_422(web_client: TestClient, name: str) -> None:
    assert web_client.post("/api/targets", json={**PING_BODY, "name": name}).status_code == 422


def test_rename_to_slash_name_422(web_client: TestClient, daemon) -> None:
    web_client.post("/api/targets", json=PING_BODY)
    assert web_client.put("/api/targets/ui-host", json={**PING_BODY, "name": "a/b"}).status_code == 422
    assert daemon.has_target("ui-host")


@pytest.mark.parametrize("name", ["x.csv", "a#b?c%d", "test", "<img src=x onerror=alert(1)>"])
def test_unusual_target_names_stay_manageable(web_client: TestClient, name: str) -> None:
    path = "/api/targets/" + quote(name, safe="")
    assert web_client.post("/api/targets", json={**PING_BODY, "name": name}).status_code == 201
    assert web_client.get(path).json()["name"] == name
    assert web_client.put(path + "/pause").status_code == 204
    assert web_client.put(path + "/resume").status_code == 204
    assert web_client.delete(path).status_code == 204


def test_target_name_is_escaped_in_pages(web_client: TestClient, seeded_state) -> None:
    name = "<img src=x onerror=alert(1)>"
    web_client.post("/api/targets", json={**PING_BODY, "name": name})
    seeded_state.update(make_result(name=name, status="UP"))
    assert name not in web_client.get("/targets").text
    assert name not in web_client.get("/").text


@pytest.mark.parametrize("host", ["-h", "-f", "1.2.3.4 -f", " x"])
def test_ping_host_that_would_be_an_option_422(web_client: TestClient, host: str) -> None:
    assert web_client.post("/api/targets", json={**PING_BODY, "host": host}).status_code == 422
    assert web_client.post("/api/targets/test", json={**PING_BODY, "host": host}).status_code == 422


@pytest.mark.parametrize("url", ["file:///etc/passwd", "chrome://version", "javascript:alert(1)", "panel.example.com"])
def test_dashboard_url_must_be_http(web_client: TestClient, url: str) -> None:
    assert web_client.post("/api/targets", json={**DASHBOARD_BODY, "url": url}).status_code == 422
    assert web_client.post("/api/targets/test", json={**DASHBOARD_BODY, "url": url}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [PING_BODY, {"type": "http", "name": "h", "url": "https://x.example"}, {"type": "tcp", "name": "t", "host": "h", "port": 1}, DASHBOARD_BODY],
)
def test_timeout_has_an_upper_limit(web_client: TestClient, body: dict) -> None:
    assert web_client.post("/api/targets", json={**body, "timeout": 120_001}).status_code == 422
    assert web_client.post("/api/targets", json={**body, "timeout": 120_000}).status_code == 201
