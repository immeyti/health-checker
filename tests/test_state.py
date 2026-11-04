from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import DashboardTarget, HttpTarget, PingTarget, SuccessIndicator, TcpTarget
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


def test_timeline_length(state):
    state.update(make_result(status="UP"))
    status = state.get_status("host-a")
    assert len(status.timeline) == 24


def test_timeline_current_hour_is_up(state):
    state.update(make_result(status="UP"))
    status = state.get_status("host-a")
    assert status.timeline[-1] == "UP"


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


def test_attempts_filter_and_purge(state):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    now = datetime.now(timezone.utc)
    state.record_attempt(make_result(checked_at=old, status="DOWN"), "old", 1)
    state.record_attempt(make_result(checked_at=now), "new", 1)
    assert [a["cycle_id"] for a in state.get_attempts("host-a", since=now - timedelta(days=1))] == ["new"]
    state.purge_old_logs(30)
    assert [a["cycle_id"] for a in state.get_attempts("host-a")] == ["new"]


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
        HttpTarget(name="http-a", url="https://site.example.com", forbidden_text="Error", timeout=3000),
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


def test_history_since_until_without_limit(state):
    now = datetime.now(timezone.utc)
    for hours in (50, 30, 10):
        state.update(make_result(checked_at=now - timedelta(hours=hours)))
    window = state.get_history("host-a", limit=None, since=now - timedelta(hours=40),
                               until=now - timedelta(hours=20))
    assert [log.checked_at for log in window] == [now - timedelta(hours=30)]
    assert len(state.get_history("host-a", limit=None)) == 3


def test_mark_network_issue_flags_whole_cycle(state):
    state.record_attempt(make_result(status="DOWN"), "c1", 1)
    state.record_attempt(make_result(status="DOWN"), "c1", 2)
    state.record_attempt(make_result(status="DOWN"), "c2", 1)
    state.mark_network_issue(["c1"], "all_targets_failed")
    assert [a["network_issue"] for a in state.get_attempts("host-a")] == [
        "all_targets_failed", "all_targets_failed", None,
    ]


# ---------------------------------------------------------------------------
# Editing and stopping UI targets
# ---------------------------------------------------------------------------


def _dashboard(name="panel", password="old-secret"):
    return DashboardTarget(
        name=name, url="https://panel.example.com/login", username="admin", password=password,
        success_indicator=SuccessIndicator(type="url_contains", value="/home"),
    )


def test_update_target_changes_config_and_keeps_history(state):
    state.add_target(PingTarget(name="srv", host="10.0.0.1"))
    state.update(make_result(name="srv", status="UP"))
    assert state.update_target("srv", PingTarget(name="srv", host="10.0.0.2", timeout=1000, max_latency=50))
    assert state.get_target("srv") == PingTarget(name="srv", host="10.0.0.2", timeout=1000, max_latency=50)
    assert len(state.get_history("srv")) == 1


def test_update_target_rename_moves_state_and_history(state):
    state.add_target(PingTarget(name="old", host="10.0.0.1"))
    state.update(make_result(name="old", status="UP"))
    state.record_attempt(make_result(name="old", status="UP"), "c1", 1)
    state.set_target_paused("old", True)

    assert state.update_target("old", PingTarget(name="new", host="10.0.0.1"))

    assert state.get_target("old") is None
    assert state.get_status("old") is None
    assert state.get_status("new").current_status == "UP"
    assert len(state.get_history("new")) == 1
    assert len(state.get_attempts("new")) == 1
    assert state.get_paused_targets() == {"new"}


def test_update_target_rename_replaces_leftover_state_row(state):
    state.add_target(PingTarget(name="old", host="10.0.0.1"))
    state.update(make_result(name="old", status="UP"))
    state.update(make_result(name="removed-from-yaml", status="DOWN"))
    assert state.update_target("old", PingTarget(name="removed-from-yaml", host="10.0.0.1"))
    assert state.get_status("removed-from-yaml").current_status == "UP"
    # The earlier target's results don't count toward this one
    assert [log.status for log in state.get_history("removed-from-yaml")] == ["UP"]


def test_update_target_encrypts_password(state):
    state.add_target(_dashboard())
    state.update_target("panel", _dashboard(password="new-secret"))
    row = state._conn.execute("SELECT config FROM targets").fetchone()
    assert "new-secret" not in row["config"]
    assert state.get_target("panel").password == "new-secret"


def test_update_target_unknown_returns_false(state):
    assert not state.update_target("nope", PingTarget(name="nope", host="10.0.0.1"))


def test_paused_targets_survive_reopening_the_database(tmp_path):
    db = str(tmp_path / "monitor.db")
    first = StateTracker(db_path=db)
    first.add_target(PingTarget(name="srv", host="10.0.0.1"))
    assert first.set_target_paused("srv", True)
    first.close()

    second = StateTracker(db_path=db)
    assert second.get_paused_targets() == {"srv"}
    assert second.set_target_paused("srv", False)
    assert second.get_paused_targets() == set()
    second.close()


def test_set_target_paused_unknown_returns_false(state):
    assert not state.set_target_paused("nope", True)


def test_status_reports_paused(state):
    state.add_target(PingTarget(name="srv", host="10.0.0.1"))
    state.update(make_result(name="srv", status="UP"))
    assert not state.get_status("srv").paused
    state.set_target_paused("srv", True)
    assert state.get_status("srv").paused
    assert state.get_all_statuses()[0].paused


def test_migration_adds_paused_and_old_rows_load_with_defaults(tmp_path):
    db = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE targets (name TEXT PRIMARY KEY, target_type TEXT NOT NULL, "
        "config TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO targets VALUES ('srv', 'ping', ?, '2026-01-01T00:00:00+00:00')",
        (json.dumps({"name": "srv", "host": "10.0.0.1"}),),
    )
    conn.commit()
    conn.close()

    state = StateTracker(db_path=db)
    assert state.get_target("srv") == PingTarget(name="srv", host="10.0.0.1", timeout=3000, max_latency=None)
    assert state.get_paused_targets() == set()
    state.close()
