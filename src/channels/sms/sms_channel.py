from __future__ import annotations

import logging

from ...models import Alert
from ..base import BaseChannel
from .base_provider import BaseSMSProvider
from .kavenegar import KavenegarProvider
from .twilio import TwilioProvider

logger = logging.getLogger(__name__)


class SMSChannel(BaseChannel):
    def __init__(self, provider: BaseSMSProvider, recipients: list[str]) -> None:
        self._provider = provider
        self._recipients = recipients

    async def send(self, alert: Alert) -> None:
        message = self._format_message(alert)
        for recipient in self._recipients:
            try:
                await self._provider.send_sms(recipient, message)
            except Exception as exc:
                logger.error("Failed to send SMS to %s: %s", recipient, exc)

    def _format_message(self, alert: Alert) -> str:
        icon = "🔴" if alert.transition == "UP_TO_DOWN" else "🟢"
        return (
            f"{icon} Monitor Alert\n"
            f"Target: {alert.target_name} ({alert.target_type})\n"
            f"Status: {alert.transition.replace('_', ' → ')}\n"
            f"Time: {alert.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    @classmethod
    def from_config(cls, config: dict) -> "SMSChannel":
        provider_name = config.get("provider", "kavenegar").lower()
        recipients = config.get("recipients", [])

        if provider_name == "kavenegar":
            kav = config.get("kavenegar", {})
            provider = KavenegarProvider(
                api_key=kav["api_key"],
                sender=kav.get("sender", ""),
            )
        elif provider_name == "twilio":
            twl = config.get("twilio", {})
            provider = TwilioProvider(
                account_sid=twl["account_sid"],
                auth_token=twl["auth_token"],
                from_number=twl["from_number"],
            )
        else:
            raise ValueError(f"Unknown SMS provider: {provider_name!r}")

        return cls(provider=provider, recipients=recipients)
