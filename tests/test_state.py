from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config import DashboardTarget, PingTarget, SuccessIndicator, TcpTarget
from src.state import StateTracker
from tests.conftest import make_result


def test_first_check_no_alert(state):
    alert = state.update(make_result(status="UP"))
    assert alert is None


def test_first_check_state_created(state):
    state.update(make_result(name="srv", status="UP"))
    statuses = state.get_all_statuses()
    assert len(statuses) == 1
    assert statuses[0].name == "srv"
    assert statuses[0].current_status == "UP"


def test_up_to_down_returns_alert(state):
    state.update(make_result(status="UP"))
    alert = state.update(make_result(status="DOWN"))
    assert alert is not None
    assert alert.transition == "UP_TO_DOWN"
    assert alert.target_name == "host-a"


def test_down_to_up_returns_alert(state):
    state.update(make_result(status="UP"))
    state.update(make_result(status="DOWN"))
    alert = state.update(make_result(status="UP"))
    assert alert is not None
    assert alert.transition == "DOWN_TO_UP"


def test_same_status_no_alert(state):
    state.update(make_result(status="UP"))
    alert = state.update(make_result(status="UP"))
    assert alert is None


def test_consecutive_failures_increment(state):
    state.update(make_result(status="UP"))
    state.update(make_result(status="DOWN"))
    state.update(make_result(status="DOWN"))
    status = state.get_status("host-a")
    assert status.consecutive_failures == 2


def test_consecutive_failures_reset_on_up(state):
    state.update(make_result(status="UP"))
    state.update(make_result(status="DOWN"))
    state.update(make_result(status="DOWN"))
    state.update(make_result(status="UP"))
    status = state.get_status("host-a")
    assert status.consecutive_failures == 0


def test_uptime_pct_calculation(state):
    for _ in range(3):
        state.update(make_result(status="UP"))
    state.update(make_result(status="DOWN"))
    status = state.get_status("host-a", retention_days=30)
    assert status.uptime_pct == 75.0


def test_timeline_24h_length(state):
    state.update(make_result(status="UP"))
    status = state.get_status("host-a")
    assert len(status.timeline_24h) == 24


def test_timeline_24h_current_hour_is_up(state):
    state.update(make_result(status="UP"))
    status = state.get_status("host-a")
    assert status.timeline_24h[-1] == "UP"


def test_purge_removes_old_entries(state):
    old_dt = datetime.now(timezone.utc) - timedelta(days=60)
    state.update(make_result(status="UP", checked_at=old_dt))
    state.update(make_result(status="UP"))  # recent
    deleted = state.purge_old_logs(retention_days=30)
    assert deleted == 1


def test_purge_keeps_recent_entries(state):
    state.update(make_result(status="UP"))
    deleted = state.purge_old_logs(retention_days=30)
    assert deleted == 0


def test_get_history_desc_order(state):
    t1 = datetime.now(timezone.utc) - timedelta(hours=2)
    t2 = datetime.now(timezone.utc) - timedelta(hours=1)
    state.update(make_result(status="UP", checked_at=t1))
    state.update(make_result(status="DOWN", checked_at=t2))
    history = state.get_history("host-a")
    assert history[0].checked_at > history[1].checked_at
    assert history[0].status == "DOWN"


# ---------------------------------------------------------------------------
# Targets added from the web UI
# ---------------------------------------------------------------------------


def _dashboard_target(name: str = "panel") -> DashboardTarget:
    return DashboardTarget(
        name=name,
        url="https://panel.example.com/login",
        username="admin",
        password="secret",
        success_indicator=SuccessIndicator(type="url_contains", value="/dashboard"),
        timeout=20000,
    )


def test_add_and_get_targets_roundtrip(state):
    targets = [
        _dashboard_target(),
        PingTarget(name="ping-a", host="10.0.0.1"),
        TcpTarget(name="tcp-a", host="10.0.0.2", port=443, timeout=3000),
    ]
    for t in targets:
        state.add_target(t)
    assert state.get_targets() == targets  # ordered by name


def test_dashboard_password_encrypted_at_rest(state):
    state.add_target(_dashboard_target())
    raw = state._conn.execute("SELECT config FROM targets").fetchone()["config"]
    assert "secret" not in raw
    assert state.get_targets()[0].password == "secret"


def test_delete_target_removes_state_and_history(state):
    state.add_target(PingTarget(name="ping-a", host="10.0.0.1"))
    state.update(make_result(name="ping-a"))
    assert state.delete_target("ping-a") is True
    assert state.get_targets() == []
    assert state.get_status("ping-a") is None
    assert state.get_history("ping-a") == []


def test_delete_unknown_target_returns_false(state):
    state.update(make_result(name="yaml-target"))
    assert state.delete_target("yaml-target") is False
    assert state.get_status("yaml-target") is not None
