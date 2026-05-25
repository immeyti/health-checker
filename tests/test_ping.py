from __future__ import annotations

import pytest

from src.checkers.ping import PingChecker
from src.config import PingTarget


async def test_ping_loopback_returns_up():
    checker = PingChecker(PingTarget(name="loopback", host="127.0.0.1"))
    result = await checker.check()
    assert result.status == "UP"
    assert result.target_name == "loopback"
    assert result.target_type == "ping"
    assert result.latency_ms is not None
    assert result.latency_ms > 0


@pytest.mark.slow
async def test_ping_unreachable_returns_down():
    """192.0.2.1 is RFC 5737 TEST-NET-1 — guaranteed non-routable. Takes ~5s."""
    checker = PingChecker(PingTarget(name="blackhole", host="192.0.2.1"))
    result = await checker.check()
    assert result.status == "DOWN"
    assert result.error_message is not None
