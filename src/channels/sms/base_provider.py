from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSMSProvider(ABC):
    @abstractmethod
    async def send_sms(self, to: str, message: str) -> None:
        ...
