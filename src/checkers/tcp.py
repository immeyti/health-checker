from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..config import TcpTarget
from ..models import CheckResult
from .base import BaseChecker

logger = logging.getLogger(__name__)


class TCPChecker(BaseChecker):
    def __init__(self, target: TcpTarget) -> None:
        self._target = target

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def target_type(self):
        return "tcp"

    async def check(self) -> CheckResult:
        timeout_s = self._target.timeout / 1000.0
        start = time.monotonic()
        checked_at = datetime.now(timezone.utc)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._target.host, self._target.port),
                timeout=timeout_s,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            writer.close()
            await writer.wait_closed()
            logger.info(
                "TCP %s (%s:%d) → UP %.1f ms",
                self._target.name, self._target.host, self._target.port, elapsed_ms,
            )
            return CheckResult(
                target_name=self._target.name,
                target_type="tcp",
                status="UP",
                latency_ms=round(elapsed_ms, 2),
                checked_at=checked_at,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "TCP %s (%s:%d) → DOWN (timeout)",
                self._target.name, self._target.host, self._target.port,
            )
            return CheckResult(
                target_name=self._target.name,
                target_type="tcp",
                status="DOWN",
                checked_at=checked_at,
                error_message=f"TCP connection timed out after {self._target.timeout}ms",
            )
        except ConnectionRefusedError:
            logger.warning(
                "TCP %s (%s:%d) → DOWN (connection refused)",
                self._target.name, self._target.host, self._target.port,
            )
            return CheckResult(
                target_name=self._target.name,
                target_type="tcp",
                status="DOWN",
                checked_at=checked_at,
                error_message="Connection refused",
            )
        except OSError as exc:
            logger.warning(
                "TCP %s (%s:%d) → DOWN (%s)",
                self._target.name, self._target.host, self._target.port, exc,
            )
            return CheckResult(
                target_name=self._target.name,
                target_type="tcp",
                status="DOWN",
                checked_at=checked_at,
                error_message=str(exc),
            )
