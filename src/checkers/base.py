from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from ..models import CheckResult


class BaseChecker(ABC):
    @abstractmethod
    async def check(self) -> CheckResult:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def target_type(self) -> Literal["ping", "dashboard", "tcp"]:
        ...
