from __future__ import annotations

from typing import Literal, Optional

import pytest

from src.channels.base import BaseChannel
from src.checkers.base import BaseChecker
from src.config import AlertsConfig, AppConfig, PingTarget, StorageConfig, WebConfig
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
