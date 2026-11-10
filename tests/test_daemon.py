from __future__ import annotations

import asyncio
import json
import socket
from typing import Literal, Optional

import pytest

from src.channels.base import BaseChannel
from src.checkers.base import BaseChecker
from src.checkers.dashboard import DashboardChecker
from src.config import (
    AlertsConfig,
    AppConfig,
    DashboardTarget,
    PingTarget,
    StorageConfig,
    SuccessIndicator,
    TcpTarget,
    WebConfig,
)
from src.connectivity import ConnectivityProbe, PublicIPLookup
from src.daemon import _PLAYWRIGHT_SEMAPHORE, MonitoringDaemon
from src.models import Alert, CheckResult
from src.state import StateTracker
from tests.conftest import make_result


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FixedResultChecker(BaseChecker):
    """Returns results from a pre-loaded list, one per call."""

    def __init__(self, results: list[CheckResult], name: str = "host-a") -> None:
        self._results = iter(results)
        self._name = name

    async def check(self) -> CheckResult:
        return next(self._results)

    @property
    def name(self) -> str:
        return self._name

    @property
    def target_type(self) -> Literal["ping", "dashboard"]:
        return "ping"


class RecordingChannel(BaseChannel):
    def __init__(self) -> None:
        self.sent_alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.sent_alerts.append(alert)

    @classmethod
    def from_config(cls, config: dict) -> "RecordingChannel":
        return cls()


class FakeConnectivity(ConnectivityProbe):
    def __init__(self, online: bool = True) -> None:
        self.online = online

    async def is_online(self) -> bool:
        return self.online


class BrokenChannel(BaseChannel):
    async def send(self, alert: Alert) -> None:
        raise RuntimeError("channel is down")

    @classmethod
    def from_config(cls, config: dict) -> "BrokenChannel":
        return cls()


def _make_daemon(
    checkers: list[BaseChecker],
    channels: list[BaseChannel],
    state: StateTracker,
    retries: int = 0,
    dashboard_retries: int = 0,
) -> MonitoringDaemon:
    """Build a MonitoringDaemon without calling _build_checkers()/_build_channels()."""
    config = AppConfig(
        check_interval=60,
        ping_targets=[PingTarget(name="loopback", host="127.0.0.1")],
        dashboard_targets=[],
        alerts=AlertsConfig(channels=[]),
        storage=StorageConfig(retention_days=30, screenshots_dir="screenshots"),
        web=WebConfig(host="127.0.0.1", port=8000),
        retries=retries,
        dashboard_retries=dashboard_retries,
        retry_delay=0,
    )
    daemon = object.__new__(MonitoringDaemon)
    daemon._config = config
    daemon._state = state
    daemon._checkers = checkers
    daemon._channels = channels
    daemon._ui_channels = {}
    daemon._connectivity = FakeConnectivity()
    daemon._public_ip = PublicIPLookup("")
    daemon._network_down = False
    daemon._paused = set()
    daemon._running = False
    return daemon


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_single_cycle_no_alert(state: StateTracker) -> None:
    checker = FixedResultChecker([make_result(status="UP")])
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state)

    await daemon._run_cycle()

    assert channel.sent_alerts == []


async def test_transition_cycle_sends_alert(state: StateTracker) -> None:
    checker = FixedResultChecker([
        make_result(status="UP"),
        make_result(status="DOWN"),
    ])
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state)

    await daemon._run_cycle()   # UP  → no alert
    await daemon._run_cycle()   # DOWN → alert

    assert len(channel.sent_alerts) == 1
    assert channel.sent_alerts[0].transition == "UP_TO_DOWN"


async def test_broken_channel_no_crash(state: StateTracker) -> None:
    checker = FixedResultChecker([
        make_result(status="UP"),
        make_result(status="DOWN"),
    ])
    broken = BrokenChannel()
    daemon = _make_daemon([checker], [broken], state)

    await daemon._run_cycle()
    await daemon._run_cycle()   # triggers alert dispatch to broken channel — must not raise


# ---------------------------------------------------------------------------
# Targets added and removed at runtime
# ---------------------------------------------------------------------------


class CloseRecordingDashboardChecker(DashboardChecker):
    def __init__(self) -> None:
        super().__init__(
            DashboardTarget(
                name="panel",
                url="https://panel.example.com/login",
                username="admin",
                password="secret",
                success_indicator=SuccessIndicator(type="url_contains", value="/dashboard"),
            )
        )
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class SelfRemovingChecker(FixedResultChecker):
    """Deletes its own target while the check is in flight."""

    def __init__(self, daemon_ref: list[MonitoringDaemon]) -> None:
        super().__init__([make_result(name="host-a", status="UP")])
        self._daemon_ref = daemon_ref

    async def check(self) -> CheckResult:
        await self._daemon_ref[0].remove_target("host-a")
        return await super().check()


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_daemon_loads_db_targets_at_startup(minimal_config, state):
    state.add_target(PingTarget(name="db-host", host="10.0.0.1"))
    daemon = MonitoringDaemon(config=minimal_config, state=state)
    assert daemon.has_target("db-host")
    assert daemon.has_target("loopback")


async def test_remove_target_closes_dashboard_browser(state):
    checker = CloseRecordingDashboardChecker()
    daemon = _make_daemon([checker], [], state)
    await daemon.remove_target("panel")
    assert checker.closed
    assert not daemon.has_target("panel")


async def test_target_removed_mid_check_is_not_recorded(state):
    daemon_ref: list[MonitoringDaemon] = []
    daemon = _make_daemon([SelfRemovingChecker(daemon_ref)], [], state)
    daemon_ref.append(daemon)
    await daemon._run_cycle()
    assert state.get_status("host-a") is None


async def test_check_once_does_not_record(minimal_config, state):
    daemon = MonitoringDaemon(config=minimal_config, state=state)
    result = await daemon.check_once(
        TcpTarget(name="tcp-test", host="127.0.0.1", port=_closed_port(), timeout=1000)
    )
    assert result.status == "DOWN"
    assert result.error_message == "Connection refused"
    assert state.get_status("tcp-test") is None


async def test_ui_channel_receives_alert(state: StateTracker) -> None:
    checker = FixedResultChecker([make_result(status="UP"), make_result(status="DOWN")])
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [], state)
    daemon._ui_channels["ops"] = channel

    await daemon._run_cycle()
    await daemon._run_cycle()

    assert len(channel.sent_alerts) == 1


def test_daemon_loads_db_channels_at_startup(minimal_config, state):
    state.add_channel("ops", "telegram", "123:TOKEN", ["42"])
    daemon = MonitoringDaemon(config=minimal_config, state=state)
    assert daemon.has_channel("ops")


# ---------------------------------------------------------------------------
# Retries before recording DOWN
# ---------------------------------------------------------------------------


class CountingChecker(FixedResultChecker):
    def __init__(self, results: list[CheckResult], target_type: str = "ping") -> None:
        super().__init__(results)
        self.calls = 0
        self._type = target_type

    async def check(self) -> CheckResult:
        self.calls += 1
        return await super().check()

    @property
    def target_type(self):
        return self._type


async def test_retry_recovers_blip_without_alert(state: StateTracker) -> None:
    checker = CountingChecker([make_result(status="UP"), make_result(status="DOWN"), make_result(status="UP")])
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state, retries=2)

    await daemon._run_cycle()  # first check: UP
    await daemon._run_cycle()  # DOWN, then UP on the first retry

    assert checker.calls == 3
    assert state.get_status("host-a").current_status == "UP"
    assert [log.status for log in state.get_history("host-a")] == ["UP", "UP"]
    assert channel.sent_alerts == []
    attempts = state.get_attempts("host-a")
    assert [(a["status"], a["attempt_number"]) for a in attempts] == [
        ("UP", 1), ("DOWN", 1), ("UP", 2),
    ]
    assert attempts[1]["cycle_id"] == attempts[2]["cycle_id"]
    assert attempts[0]["cycle_id"] != attempts[1]["cycle_id"]


async def test_down_after_all_retries_alerts(state: StateTracker) -> None:
    results = [make_result(status="UP")] + [make_result(status="DOWN")] * 3
    checker = CountingChecker(results)
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state, retries=2)

    await daemon._run_cycle()
    await daemon._run_cycle()

    assert checker.calls == 4
    assert state.get_status("host-a").current_status == "DOWN"
    assert len(channel.sent_alerts) == 1
    assert channel.sent_alerts[0].transition == "UP_TO_DOWN"


async def test_first_cycle_down_after_retries_alerts(state: StateTracker) -> None:
    checker = CountingChecker([make_result(status="DOWN")] * 3)
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state, retries=2)

    await daemon._run_cycle()

    assert checker.calls == 3
    assert state.get_status("host-a").current_status == "DOWN"
    assert [alert.transition for alert in channel.sent_alerts] == ["INITIAL_DOWN"]


async def test_dashboard_uses_dashboard_retries(state: StateTracker) -> None:
    checker = CountingChecker([make_result(status="DOWN")] * 3, target_type="dashboard")
    daemon = _make_daemon([checker], [], state, retries=2, dashboard_retries=1)

    await daemon._run_cycle()

    assert checker.calls == 2


# ---------------------------------------------------------------------------
# Monitor's own network problems
# ---------------------------------------------------------------------------


def _site(name: str, *statuses: str) -> FixedResultChecker:
    return FixedResultChecker([make_result(name=name, status=s) for s in statuses], name=name)


async def test_all_targets_down_while_monitor_online_records_each_failure(state: StateTracker) -> None:
    checkers = [_site("a", "UP", "DOWN", "UP"), _site("b", "UP", "DOWN", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)

    await daemon._run_cycle()  # both UP
    await daemon._run_cycle()  # both DOWN together

    assert [(a.target_name, a.transition) for a in channel.sent_alerts] == [
        ("a", "UP_TO_DOWN"), ("b", "UP_TO_DOWN"),
    ]
    assert state.get_status("a").current_status == "DOWN"
    assert [log.status for log in state.get_history("a")] == ["DOWN", "UP"]
    attempts = state.get_attempts("a")  # the failed attempt is still kept
    assert [(a["status"], a["network_issue"]) for a in attempts] == [
        ("UP", None), ("DOWN", None),
    ]

    await daemon._run_cycle()  # back UP

    assert [(a.target_name, a.transition) for a in channel.sent_alerts] == [
        ("a", "UP_TO_DOWN"), ("b", "UP_TO_DOWN"),
        ("a", "DOWN_TO_UP"), ("b", "DOWN_TO_UP"),
    ]


async def test_network_alert_sent_once_while_down(state: StateTracker) -> None:
    checkers = [_site("a", "UP", "DOWN", "DOWN"), _site("b", "UP", "DOWN", "DOWN")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)
    daemon._connectivity = FakeConnectivity(online=False)

    for _ in range(3):
        await daemon._run_cycle()

    assert len(channel.sent_alerts) == 1


async def test_one_target_down_with_other_target_up_is_recorded(state: StateTracker) -> None:
    checkers = [_site("a", "UP", "DOWN"), _site("b", "UP", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)

    await daemon._run_cycle()
    daemon._connectivity = FakeConnectivity(online=False)
    await daemon._run_cycle()

    assert [(a.target_name, a.transition) for a in channel.sent_alerts] == [
        ("a", "UP_TO_DOWN"),
    ]
    assert state.get_attempts("a")[-1]["network_issue"] is None
    assert state.get_status("a").current_status == "DOWN"
    assert [log.status for log in state.get_history("b")] == ["UP", "UP"]


async def test_one_target_down_while_online_alerts_normally(state: StateTracker) -> None:
    checkers = [_site("a", "UP", "DOWN"), _site("b", "UP", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)

    await daemon._run_cycle()
    await daemon._run_cycle()

    assert [(a.target_name, a.transition) for a in channel.sent_alerts] == [("a", "UP_TO_DOWN")]
    assert state.get_status("a").current_status == "DOWN"
    assert state.get_attempts("a")[-1]["network_issue"] is None


async def test_connectivity_probe_reports_reachability() -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    open_port = server.sockets[0].getsockname()[1]
    async with server:
        assert await ConnectivityProbe([f"127.0.0.1:{_closed_port()}", f"127.0.0.1:{open_port}"]).is_online()
        assert not await ConnectivityProbe([f"127.0.0.1:{_closed_port()}"], timeout=1).is_online()
        assert await ConnectivityProbe([]).is_online()


async def test_attempts_record_monitor_public_ip(state: StateTracker) -> None:
    daemon = _make_daemon([_site("a", "UP")], [], state)
    daemon._public_ip.value = "198.51.100.4"

    await daemon._run_cycle()

    assert json.loads(state.get_attempts("a")[0]["diagnostics"])["source_ip"] == "198.51.100.4"


# ---------------------------------------------------------------------------
# Editing and stopping targets
# ---------------------------------------------------------------------------


class SlowChecker(CountingChecker):
    """Successful result with a fixed latency, limited by max_latency."""

    def __init__(self, latencies: list[float], max_latency: Optional[int]) -> None:
        super().__init__([make_result(status="UP", latency_ms=ms) for ms in latencies])
        self._max_latency = max_latency

    @property
    def max_latency(self) -> Optional[int]:
        return self._max_latency


class SelfPausingChecker(FixedResultChecker):
    """Stops its own target while the check is in flight."""

    def __init__(self, daemon_ref: list[MonitoringDaemon]) -> None:
        super().__init__([make_result(name="host-a", status="DOWN")])
        self._daemon_ref = daemon_ref

    async def check(self) -> CheckResult:
        await self._daemon_ref[0].pause_target("host-a")
        return await super().check()


async def test_paused_target_is_not_checked_until_resumed(state):
    checker = CountingChecker([make_result(status="UP"), make_result(status="UP")])
    daemon = _make_daemon([checker], [], state)
    await daemon.pause_target("host-a")

    await daemon._run_cycle()
    assert checker.calls == 0
    assert state.get_status("host-a") is None

    daemon.resume_target("host-a")
    await daemon._run_cycle()
    assert checker.calls == 1
    assert state.get_status("host-a").current_status == "UP"


async def test_pause_closes_dashboard_browser(state):
    checker = CloseRecordingDashboardChecker()
    daemon = _make_daemon([checker], [], state)
    await daemon.pause_target("panel")
    assert checker.closed
    assert daemon.has_target("panel")


async def test_target_paused_mid_check_records_nothing(state):
    daemon_ref: list[MonitoringDaemon] = []
    channel = RecordingChannel()
    daemon = _make_daemon([SelfPausingChecker(daemon_ref)], [channel], state, retries=2)
    daemon_ref.append(daemon)
    await daemon._run_cycle()
    assert state.get_status("host-a") is None
    assert state.get_attempts("host-a") == []
    assert channel.sent_alerts == []


def test_daemon_loads_paused_targets_at_startup(minimal_config, state):
    state.add_target(PingTarget(name="db-host", host="10.0.0.1"))
    state.set_target_paused("db-host", True)
    daemon = MonitoringDaemon(config=minimal_config, state=state)
    assert daemon.has_target("db-host")
    assert daemon._paused == {"db-host"}


async def test_network_guard_ignores_paused_targets(state):
    checkers = [_site("a", "UP", "DOWN"), _site("b", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)
    await daemon._run_cycle()  # both UP
    await daemon.pause_target("b")
    await daemon._run_cycle()  # only "a" runs, and one DOWN isn't "all targets failed"
    assert [(a.target_name, a.transition) for a in channel.sent_alerts] == [("a", "UP_TO_DOWN")]


async def test_stopping_every_target_does_not_report_network_restored(state):
    channel = RecordingChannel()
    daemon = _make_daemon([_site("a")], [channel], state)
    daemon._network_down = True
    await daemon.pause_target("a")
    await daemon._run_cycle()
    assert channel.sent_alerts == []
    assert daemon._network_down


async def test_removed_target_is_no_longer_paused(state):
    daemon = _make_daemon([_site("a")], [], state)
    await daemon.pause_target("a")
    await daemon.remove_target("a")
    assert daemon._paused == set()


async def test_replace_target_swaps_checker_and_closes_old_browser(minimal_config, state):
    old = CloseRecordingDashboardChecker()
    daemon = _make_daemon([old], [], state)
    await daemon.replace_target("panel", PingTarget(name="renamed", host="10.0.0.1"))
    assert old.closed
    assert not daemon.has_target("panel")
    assert daemon.has_target("renamed")


async def test_replace_target_keeps_it_paused_under_new_name(state):
    daemon = _make_daemon([_site("a")], [], state)
    await daemon.pause_target("a")
    await daemon.replace_target("a", PingTarget(name="b", host="10.0.0.1"))
    assert daemon._paused == {"b"}


async def test_max_latency_exceeded_is_down_after_retries_and_alerts(state):
    checker = SlowChecker([10, 500, 500], max_latency=100)
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state, retries=1)

    await daemon._run_cycle()  # 10 ms: UP
    await daemon._run_cycle()  # 500 ms twice: DOWN

    assert checker.calls == 3
    status = state.get_status("host-a")
    assert status.current_status == "DOWN"
    assert status.latency_ms == 500
    assert [a.transition for a in channel.sent_alerts] == ["UP_TO_DOWN"]
    assert "exceeds limit 100 ms" in channel.sent_alerts[0].message


async def test_max_latency_blip_recovers_on_retry(state):
    checker = SlowChecker([500, 10], max_latency=100)
    daemon = _make_daemon([checker], [], state, retries=1)
    await daemon._run_cycle()
    assert state.get_status("host-a").current_status == "UP"
    assert [a["status"] for a in state.get_attempts("host-a")] == ["DOWN", "UP"]


async def test_no_max_latency_means_no_limit(state):
    daemon = _make_daemon([SlowChecker([99999], max_latency=None)], [], state)
    await daemon._run_cycle()
    assert state.get_status("host-a").current_status == "UP"


async def test_check_once_applies_max_latency(state):
    daemon = _make_daemon([], [], state)
    daemon._make_checker = lambda target: SlowChecker([500], max_latency=100)
    result = await daemon.check_once(PingTarget(name="host-a", host="10.0.0.1"))
    assert result.status == "DOWN"
    assert result.error_message == "Latency 500 ms exceeds limit 100 ms"


async def test_all_down_with_a_stopped_target_is_real_when_monitor_is_online(state):
    """The stopped target may have been the one up, so "all failed" can't blame the network."""
    checkers = [_site("a", "UP", "DOWN"), _site("b", "UP", "DOWN"), _site("c", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)
    await daemon._run_cycle()
    await daemon.pause_target("c")
    await daemon._run_cycle()
    assert sorted(a.target_name for a in channel.sent_alerts) == ["a", "b"]
    assert state.get_status("a").current_status == "DOWN"


async def test_all_down_with_a_stopped_target_and_monitor_offline_is_network(state):
    checkers = [_site("a", "UP", "DOWN"), _site("b", "UP", "DOWN"), _site("c", "UP")]
    channel = RecordingChannel()
    daemon = _make_daemon(checkers, [channel], state)
    await daemon._run_cycle()
    await daemon.pause_target("c")
    daemon._connectivity.online = False
    await daemon._run_cycle()
    assert [a.target_name for a in channel.sent_alerts] == ["Monitor network"]
    assert state.get_status("a").current_status == "UP"


class EditDuringOnlineProbe(FakeConnectivity):
    """Runs a web UI action at the moment the cycle awaits the connectivity probe."""

    def __init__(self, action) -> None:
        super().__init__()
        self._action = action

    async def is_online(self) -> bool:
        await self._action()
        return True


async def test_same_name_edit_after_check_discards_stale_result(state):
    channel = RecordingChannel()
    daemon = _make_daemon([_site("a", "UP", "DOWN")], [channel], state)
    await daemon._run_cycle()

    async def edit():
        await daemon.replace_target("a", PingTarget(name="a", host="10.0.0.1"))

    daemon._connectivity = EditDuringOnlineProbe(edit)
    await daemon._run_cycle()

    assert channel.sent_alerts == []
    assert state.get_status("a").current_status == "UP"


class QueuedDashboardChecker(CloseRecordingDashboardChecker):
    def __init__(self) -> None:
        super().__init__()
        self.checks = 0

    async def check(self) -> CheckResult:
        self.checks += 1
        return make_result(name="panel", target_type="dashboard", status="UP")


async def test_queued_dashboard_check_does_not_log_in_after_stop(state):
    checker = QueuedDashboardChecker()
    daemon = _make_daemon([checker], [], state)
    await _PLAYWRIGHT_SEMAPHORE.acquire()  # the check will queue here
    try:
        cycle = asyncio.create_task(daemon._run_cycle())
        await asyncio.sleep(0)
        await daemon.pause_target("panel")
    finally:
        _PLAYWRIGHT_SEMAPHORE.release()
    await cycle
    assert checker.checks == 0
    assert state.get_attempts("panel") == []


class DiesWhenBrowserCloses(CloseRecordingDashboardChecker):
    """A login in flight: it fails as soon as its browser is closed."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.released = asyncio.Event()

    async def check(self) -> CheckResult:
        self.started.set()
        await self.released.wait()
        raise RuntimeError("Target page, context or browser has been closed")


async def test_stop_then_quick_resume_does_not_record_killed_login(state):
    checker = DiesWhenBrowserCloses()
    channel = RecordingChannel()
    daemon = _make_daemon([checker], [channel], state)
    state.update(make_result(name="panel", target_type="dashboard", status="UP"))

    cycle = asyncio.create_task(daemon._run_cycle())
    await checker.started.wait()
    await daemon.pause_target("panel")
    daemon.resume_target("panel")
    checker.released.set()
    await cycle

    assert checker.closed
    assert daemon.has_target("panel")
    assert channel.sent_alerts == []
    assert state.get_status("panel").current_status == "UP"
    assert state.get_attempts("panel") == []
