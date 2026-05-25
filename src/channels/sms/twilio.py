from __future__ import annotations

import logging

import httpx

from .base_provider import BaseSMSProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


class TwilioProvider(BaseSMSProvider):
    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number

    async def send_sms(self, to: str, message: str) -> None:
        url = _BASE_URL.format(account_sid=self._account_sid)
        payload = {
            "To": to,
            "From": self._from_number,
            "Body": message,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                data=payload,
                auth=(self._account_sid, self._auth_token),
            )
            if resp.status_code not in (200, 201):
                logger.error(
                    "Twilio SMS to %s failed: HTTP %d — %s",
                    to, resp.status_code, resp.text[:200],
                )
            else:
                logger.info("Twilio SMS sent to %s", to)
