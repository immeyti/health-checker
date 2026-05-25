from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Alert


class BaseChannel(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        ...

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict) -> "BaseChannel":
        ...
