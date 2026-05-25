from __future__ import annotations

import asyncio

import pytest

from src.checkers.tcp import TCPChecker
from src.config import TcpTarget


async def _free_port() -> int:
    """Ask the OS for a free port, then release it."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


async def test_tcp_open_port_returns_up():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        checker = TCPChecker(TcpTarget(name="loopback", host="127.0.0.1", port=port))
        result = await checker.check()
        assert result.status == "UP"
        assert result.target_name == "loopback"
        assert result.target_type == "tcp"
        assert result.latency_ms is not None and result.latency_ms > 0
        assert result.error_message is None
    finally:
        server.close()
        await server.wait_closed()


async def test_tcp_refused_port_returns_down():
    port = await _free_port()
    # Port is now closed — OS will refuse the connection immediately
    checker = TCPChecker(TcpTarget(name="refused", host="127.0.0.1", port=port))
    result = await checker.check()
    assert result.status == "DOWN"
    assert result.error_message == "Connection refused"


@pytest.mark.slow
async def test_tcp_timeout_returns_down():
    # 192.0.2.1 is RFC 5737 TEST-NET-1 — packets are silently dropped
    checker = TCPChecker(TcpTarget(name="blackhole", host="192.0.2.1", port=443, timeout=3000))
    result = await checker.check()
    assert result.status == "DOWN"
    assert "timed out" in result.error_message
