from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class ConnectivityProbe:
    """Tells whether the monitor itself can reach the internet.

    Online if a TCP connection to any of the reference endpoints ("host:port")
    succeeds. An empty list disables the probe (always online).
    """

    def __init__(self, endpoints: list[str], timeout: float = 3.0) -> None:
        self._endpoints = endpoints
        self._timeout = timeout

    async def is_online(self) -> bool:
        if not self._endpoints:
            return True
        results = await asyncio.gather(*(self._reach(e) for e in self._endpoints))
        return any(results)

    async def _reach(self, endpoint: str) -> bool:
        host, port = endpoint.rsplit(":", 1)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=self._timeout
            )
        except (OSError, asyncio.TimeoutError):
            logger.info("Connectivity probe: %s unreachable", endpoint)
            return False
        writer.close()
        return True


class PublicIPLookup:
    """Caches the monitor's public IP — the source address target servers see."""

    def __init__(self, url: str, refresh_seconds: int = 600) -> None:
        self._url = url
        self._refresh_seconds = refresh_seconds
        self._fetched_at: float | None = None
        self.value: str | None = None

    async def refresh(self) -> None:
        """Re-fetch when stale; keep the last known IP if the lookup fails."""
        if not self._url:
            return
        if self._fetched_at and time.monotonic() - self._fetched_at < self._refresh_seconds:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
            self.value = str(ipaddress.ip_address(resp.text.strip()))
            self._fetched_at = time.monotonic()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Public IP lookup failed: %s", exc)
