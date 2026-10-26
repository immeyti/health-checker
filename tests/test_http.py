from __future__ import annotations

import httpx
import respx

from src.checkers.http import HTTPChecker
from src.config import HttpTarget

URL = "https://site.example.com/"


class FixedDNSChecker(HTTPChecker):
    """Answers DNS lookups for failed checks without touching the network."""

    async def _resolve(self, host):
        return "203.0.113.7"


def _checker(**kwargs) -> HTTPChecker:
    return FixedDNSChecker(HttpTarget(name="site", url=URL, **kwargs))


@respx.mock
async def test_http_200_returns_up():
    respx.get(URL).mock(return_value=httpx.Response(200, text="ok"))
    result = await _checker().check()
    assert result.status == "UP"
    assert result.target_type == "http"
    assert result.latency_ms is not None
    assert result.error_message is None


@respx.mock
async def test_http_500_returns_down_with_status():
    respx.get(URL).mock(return_value=httpx.Response(500))
    result = await _checker().check()
    assert result.status == "DOWN"
    assert result.error_message == "HTTP 500 Internal Server Error"
    assert result.diagnostics["http_status"] == 500
    assert result.diagnostics["request_host"] == "site.example.com"
    assert result.diagnostics["duration_ms"] >= 0


@respx.mock
async def test_http_404_returns_down():
    respx.get(URL).mock(return_value=httpx.Response(404))
    result = await _checker().check()
    assert result.status == "DOWN"
    assert result.error_message == "HTTP 404 Not Found"


@respx.mock
async def test_http_redirect_to_healthy_page_returns_up():
    respx.get(URL).mock(return_value=httpx.Response(301, headers={"Location": URL + "home"}))
    respx.get(URL + "home").mock(return_value=httpx.Response(200))
    result = await _checker().check()
    assert result.status == "UP"


@respx.mock
async def test_http_timeout_returns_down():
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    result = await _checker(timeout=2000).check()
    assert result.status == "DOWN"
    assert result.error_message == "Request timed out after 2000ms"
    assert result.latency_ms is None
    assert result.diagnostics["exception_type"] == "ConnectTimeout"
    assert result.diagnostics["resolved_ips"] == "203.0.113.7"


@respx.mock
async def test_http_connection_error_returns_down():
    respx.get(URL).mock(side_effect=httpx.ConnectError("Name or service not known"))
    result = await _checker().check()
    assert result.status == "DOWN"
    assert "Name or service not known" in result.error_message


@respx.mock
async def test_http_expected_text_present_up_missing_down():
    respx.get(URL).mock(return_value=httpx.Response(200, text="<h1>Welcome</h1>"))
    assert (await _checker(expected_text="Welcome").check()).status == "UP"
    result = await _checker(expected_text="Dashboard").check()
    assert result.status == "DOWN"
    assert result.error_message == 'Expected text "Dashboard" not found'


@respx.mock
async def test_http_forbidden_text_found_returns_down():
    respx.get(URL).mock(return_value=httpx.Response(200, text="Database error: connection lost"))
    result = await _checker(forbidden_text="Database error").check()
    assert result.status == "DOWN"
    assert result.error_message == 'Forbidden text "Database error" found'
