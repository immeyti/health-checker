from __future__ import annotations

import logging
from typing import Optional

import httpx

from ...models import Alert
from ..base import BaseChannel, format_alert_time

logger = logging.getLogger(__name__)

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel(BaseChannel):
    def __init__(self, bot_token: str, chat_ids: list[str], topic_id: Optional[int] = None) -> None:
        self._bot_token = bot_token
        self._chat_ids = chat_ids
        self._topic_id = topic_id  # forum topic (message_thread_id); None = General

    async def send(self, alert: Alert) -> None:
        message = self._format_message(alert)
        url = _SEND_URL.format(token=self._bot_token)
        async with httpx.AsyncClient(timeout=10) as client:
            for chat_id in self._chat_ids:
                try:
                    payload = {"chat_id": chat_id, "text": message}
                    if self._topic_id is not None:
                        payload["message_thread_id"] = self._topic_id
                    resp = await client.post(url, json=payload)
                    if resp.status_code != 200:
                        logger.error(
                            "Telegram message to %s failed: HTTP %d — %s",
                            chat_id, resp.status_code, resp.text[:200],
                        )
                    else:
                        logger.info("Telegram message sent to %s", chat_id)
                except Exception as exc:
                    logger.error("Failed to send Telegram message to %s: %s", chat_id, exc)

    def _format_message(self, alert: Alert) -> str:
        if alert.target_type == "network":
            return self._format_network_alert(alert)
        icon = "🔴" if alert.transition == "UP_TO_DOWN" else "🟢"
        return (
            f"{icon} Monitor Alert\n"
            f"Target: {alert.target_name} ({alert.target_type})\n"
            f"Status: {alert.transition.replace('_', ' → ')}\n"
            f"Time: {format_alert_time(alert.triggered_at)}"
        )

    @classmethod
    def from_config(cls, config: dict) -> "TelegramChannel":
        telegram = config.get("telegram", {})
        return cls(
            bot_token=telegram["bot_token"],
            chat_ids=config.get("chat_ids", []),
            topic_id=telegram.get("topic_id"),
        )
