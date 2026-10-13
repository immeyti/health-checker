from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

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
        try:
            async with httpx.AsyncClient(
                timeout=self._target.timeout / 1000.0, follow_redirects=True
            ) as client:
                resp = await client.get(self._target.url)
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            error = self._verify(resp)
        except httpx.TimeoutException:
            elapsed_ms = None
            error = f"Request timed out after {self._target.timeout}ms"
        except httpx.HTTPError as exc:
            elapsed_ms = None
            error = str(exc) or type(exc).__name__

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
