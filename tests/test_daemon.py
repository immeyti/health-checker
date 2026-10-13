from __future__ import annotations

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
from src.daemon import MonitoringDaemon
from src.models import Alert, CheckResult
from src.state import StateTracker
from tests.conftest import make_result


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FixedResultChecker(BaseChecker):
    """Returns results from a pre-loaded list, one per call."""

    def __init__(self, results: list[CheckResult]) -> None:
        self._results = iter(results)

    async def check(self) -> CheckResult:
        return next(self._results)

    @property
    def name(self) -> str:
        return "host-a"

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
) -> MonitoringDaemon:
    """Build a MonitoringDaemon without calling _build_checkers()/_build_channels()."""
    config = AppConfig(
        check_interval=60,
        ping_targets=[PingTarget(name="loopback", host="127.0.0.1")],
        dashboard_targets=[],
        alerts=AlertsConfig(channels=[]),
        storage=StorageConfig(retention_days=30, screenshots_dir="screenshots"),
        web=WebConfig(host="127.0.0.1", port=8000),
    )
    daemon = object.__new__(MonitoringDaemon)
    daemon._config = config
    daemon._state = state
    daemon._checkers = checkers
    daemon._channels = channels
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
