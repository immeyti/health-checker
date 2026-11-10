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


async def test_ping_failure_returns_down(monkeypatch):
    async def failed_ping():
        raise RuntimeError("ping exited with code 1")

    checker = PingChecker(PingTarget(name="blackhole", host="192.0.2.1"))
    monkeypatch.setattr(checker, "_run_ping", failed_ping)
    result = await checker.check()
    assert result.status == "DOWN"
    assert result.error_message == "ping exited with code 1"


@pytest.mark.parametrize("timeout,seconds", [(3000, "3"), (1500, "2"), (200, "1")])
def test_ping_wait_follows_timeout(monkeypatch, timeout, seconds):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    checker = PingChecker(PingTarget(name="x", host="10.0.0.1", timeout=timeout))
    assert checker._ping_args() == ["ping", "-c", "1", "-W", seconds, "10.0.0.1"]


async def test_ping_over_max_latency_is_down():
    checker = PingChecker(PingTarget(name="loopback", host="127.0.0.1", max_latency=1))
    checker._parse_latency = lambda stdout: 5.0
    result = await checker.run()
    assert result.status == "DOWN"
    assert result.latency_ms == 5.0
    assert result.error_message == "Latency 5 ms exceeds limit 1 ms"
