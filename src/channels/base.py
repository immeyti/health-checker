from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import Alert


_TEHRAN = ZoneInfo("Asia/Tehran")


def format_alert_time(dt: datetime) -> str:
    """Alert timestamps are shown in Tehran time."""
    return dt.astimezone(_TEHRAN).strftime("%Y-%m-%d %H:%M Tehran")


class BaseChannel(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        ...

    @staticmethod
    def _format_network_alert(alert: Alert) -> str:
        """Text for the monitor's own connection going down / coming back."""
        if alert.transition == "UP_TO_DOWN":
            title = "⚠️ Monitor network problem"
            note = "Site alerts are paused until the monitor's connection recovers."
        else:
            title = "✅ Monitor network restored"
            note = ""
        return "\n".join(filter(None, [
            title,
            alert.message,
            note,
            f"Time: {format_alert_time(alert.triggered_at)}",
        ]))

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict) -> "BaseChannel":
        ...
