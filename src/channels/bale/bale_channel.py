from __future__ import annotations

import logging

import httpx

from ...models import Alert
from ..base import BaseChannel

logger = logging.getLogger(__name__)

_SEND_URL = "https://tapi.bale.ai/bot{token}/sendMessage"


class BaleChannel(BaseChannel):
    def __init__(self, bot_token: str, chat_ids: list[str]) -> None:
        self._bot_token = bot_token
        self._chat_ids = chat_ids

    async def send(self, alert: Alert) -> None:
        message = self._format_message(alert)
        url = _SEND_URL.format(token=self._bot_token)
        async with httpx.AsyncClient(timeout=10) as client:
            for chat_id in self._chat_ids:
                try:
                    resp = await client.post(url, json={"chat_id": chat_id, "text": message})
                    if resp.status_code != 200:
                        logger.error(
                            "Bale message to %s failed: HTTP %d — %s",
                            chat_id, resp.status_code, resp.text[:200],
                        )
                    else:
                        logger.info("Bale message sent to %s", chat_id)
                except Exception as exc:
                    logger.error("Failed to send Bale message to %s: %s", chat_id, exc)

    def _format_message(self, alert: Alert) -> str:
        icon = "🔴" if alert.transition == "UP_TO_DOWN" else "🟢"
        return (
            f"{icon} Monitor Alert\n"
            f"Target: {alert.target_name} ({alert.target_type})\n"
            f"Status: {alert.transition.replace('_', ' → ')}\n"
            f"Time: {alert.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    @classmethod
    def from_config(cls, config: dict) -> "BaleChannel":
        bale = config.get("bale", {})
        return cls(
            bot_token=bale["bot_token"],
            chat_ids=config.get("chat_ids", []),
        )
