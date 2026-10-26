from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from ..config import HttpTarget
from ..models import CheckResult
from .base import BaseChecker

logger = logging.getLogger(__name__)


class HTTPChecker(BaseChecker):
    def __init__(self, target: HttpTarget) -> None:
        self._target = target

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def target_type(self):
        return "http"

    async def check(self) -> CheckResult:
        start = time.monotonic()
        checked_at = datetime.now(timezone.utc)
        diagnostics: dict[str, str | int | float | None] = {
            "request_host": urlsplit(self._target.url).hostname,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._target.timeout / 1000.0, follow_redirects=True
            ) as client:
                resp = await client.get(self._target.url)
                # Read the peer address while the connection is still open
                if stream := resp.extensions.get("network_stream"):
                    if server_addr := stream.get_extra_info("server_addr"):
                        diagnostics["server_ip"] = server_addr[0]
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            diagnostics.update({
                "http_status": resp.status_code,
                "response_host": resp.url.host,
                "http_version": resp.http_version,
                "redirect_count": len(resp.history),
            })
            for header in ("date", "server", "cf-ray", "x-request-id"):
                if value := resp.headers.get(header):
                    diagnostics[header.replace("-", "_")] = value[:200]
            error = self._verify(resp)
        except httpx.TimeoutException as exc:
            elapsed_ms = None
            error = f"Request timed out after {self._target.timeout}ms"
            diagnostics["exception_type"] = type(exc).__name__
        except httpx.HTTPError as exc:
            elapsed_ms = None
            error = str(exc) or type(exc).__name__
            diagnostics["exception_type"] = type(exc).__name__

        diagnostics["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
        if error and "server_ip" not in diagnostics:
            diagnostics["resolved_ips"] = await self._resolve(diagnostics["request_host"])

        status = "DOWN" if error else "UP"
        if error:
            logger.warning("HTTP %s (%s) → DOWN (%s)", self._target.name, self._target.url, error)
        else:
            logger.info("HTTP %s (%s) → UP %.1f ms", self._target.name, self._target.url, elapsed_ms)
        return CheckResult(
            target_name=self._target.name,
            target_type="http",
            status=status,
            latency_ms=elapsed_ms,
            checked_at=checked_at,
            error_message=error,
            diagnostics=diagnostics,
        )

    def _verify(self, resp: httpx.Response) -> Optional[str]:
        """Return the reason the response counts as DOWN, or None if it's healthy."""
        if resp.status_code >= 400:
            return f"HTTP {resp.status_code} {resp.reason_phrase}"
        if self._target.expected_text and self._target.expected_text not in resp.text:
            return f'Expected text "{self._target.expected_text}" not found'
        if self._target.forbidden_text and self._target.forbidden_text in resp.text:
            return f'Forbidden text "{self._target.forbidden_text}" found'
        return None

    async def _resolve(self, host: Optional[str]) -> Optional[str]:
        """IPs the host resolves to now — shows which servers a failed request was aimed at."""
        if not host:
            return None
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None), 2)
        except (OSError, asyncio.TimeoutError):
            return None
        return " ".join(sorted({info[4][0] for info in infos}))
