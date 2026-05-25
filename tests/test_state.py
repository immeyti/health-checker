from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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
