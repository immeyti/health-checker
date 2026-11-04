from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, Optional

from ..models import CheckResult


class BaseChecker(ABC):
    @abstractmethod
    async def check(self) -> CheckResult:
        ...

    async def run(self) -> CheckResult:
        """check(), with a successful result over the latency limit turned into DOWN."""
        result = await self.check()
        limit = self.max_latency
        if limit and result.status == "UP" and result.latency_ms is not None and result.latency_ms > limit:
            result.status = "DOWN"
            result.error_message = f"Latency {result.latency_ms:.0f} ms exceeds limit {limit} ms"
        return result

    @property
    def max_latency(self) -> Optional[int]:
        return None

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def target_type(self) -> Literal["ping", "dashboard", "tcp", "http"]:
        ...
