from __future__ import annotations

import asyncio
import logging
import math
import platform
import re
import time
from datetime import datetime, timezone
from typing import Optional

from ..config import PingTarget
from ..models import CheckResult
from .base import BaseChecker

logger = logging.getLogger(__name__)

# Regex patterns to extract RTT from ping output (Linux/macOS and Windows)
_RTT_PATTERNS = [
    re.compile(r"time[=<]([\d.]+)\s*ms"),          # Linux/macOS: time=1.23 ms
    re.compile(r"Average\s*=\s*([\d.]+)ms"),        # Windows: Average = 1ms
]

# Extra time for the ping process to exit on its own before it is abandoned
_EXIT_GRACE_SECONDS = 2


class PingChecker(BaseChecker):
    def __init__(self, target: PingTarget) -> None:
        self._target = target

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def max_latency(self):
        return self._target.max_latency

    @property
    def target_type(self):
        return "ping"

    async def check(self) -> CheckResult:
        start = time.monotonic()
        checked_at = datetime.now(timezone.utc)
        try:
            stdout = await asyncio.wait_for(
                self._run_ping(), timeout=self._target.timeout / 1000.0 + _EXIT_GRACE_SECONDS
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            latency = self._parse_latency(stdout) or elapsed_ms
            logger.info("PING %s (%s) → UP %.1f ms", self._target.name, self._target.host, latency)
            return CheckResult(
                target_name=self._target.name,
                target_type="ping",
                status="UP",
                latency_ms=round(latency, 2),
                checked_at=checked_at,
            )
        except asyncio.TimeoutError:
            logger.warning("PING %s (%s) → DOWN (timeout)", self._target.name, self._target.host)
            return CheckResult(
                target_name=self._target.name,
                target_type="ping",
                status="DOWN",
                checked_at=checked_at,
                error_message="Ping timed out",
            )
        except Exception as exc:
            logger.warning("PING %s (%s) → DOWN (%s)", self._target.name, self._target.host, exc)
            return CheckResult(
                target_name=self._target.name,
                target_type="ping",
                status="DOWN",
                checked_at=checked_at,
                error_message=str(exc),
            )

    async def _run_ping(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            *self._ping_args(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await proc.communicate()
        stdout = stdout_bytes.decode(errors="replace")

        if proc.returncode != 0:
            raise RuntimeError(f"ping exited with code {proc.returncode}")
        return stdout

    def _ping_args(self) -> list[str]:
        timeout = self._target.timeout
        if platform.system() == "Windows":
            return ["ping", "-n", "1", "-w", str(timeout), self._target.host]
        # Linux -W takes whole seconds
        return ["ping", "-c", "1", "-W", str(max(1, math.ceil(timeout / 1000))), self._target.host]

    def _parse_latency(self, stdout: str) -> Optional[float]:
        for pattern in _RTT_PATTERNS:
            m = pattern.search(stdout)
            if m:
                return float(m.group(1))
        return None
