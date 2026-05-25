from __future__ import annotations

import logging

import httpx

from .base_provider import BaseSMSProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.kavenegar.com/v1/{api_key}/sms/send.json"


class KavenegarProvider(BaseSMSProvider):
    def __init__(self, api_key: str, sender: str) -> None:
        self._api_key = api_key
        self._sender = sender

    async def send_sms(self, to: str, message: str) -> None:
        url = _BASE_URL.format(api_key=self._api_key)
        payload = {
            "receptor": to,
            "sender": self._sender,
            "message": message,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data=payload)
            if resp.status_code != 200:
                logger.error(
                    "Kavenegar SMS to %s failed: HTTP %d — %s",
                    to, resp.status_code, resp.text[:200],
                )
                return
            data = resp.json()
            return_code = data.get("return", {}).get("status")
            if return_code != 200:
                logger.error(
                    "Kavenegar SMS to %s failed: API status %s — %s",
                    to, return_code, data.get("return", {}).get("message"),
                )
            else:
                logger.info("Kavenegar SMS sent to %s", to)
