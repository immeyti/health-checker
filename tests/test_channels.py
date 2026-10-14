from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.channels import build_channel
from src.channels.sms.base_provider import BaseSMSProvider
from src.channels.sms.sms_channel import SMSChannel
from src.channels.telegram.telegram_channel import TelegramChannel
from src.models import Alert


def _make_alert(**kwargs) -> Alert:
    defaults = dict(
        target_name="srv",
        target_type="ping",
        transition="UP_TO_DOWN",
        triggered_at=datetime.now(timezone.utc),
        message="[UP_TO_DOWN] srv (ping) is now DOWN",
    )
    defaults.update(kwargs)
    return Alert(**defaults)


class RecordingProvider(BaseSMSProvider):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def send_sms(self, to: str, message: str) -> None:
        self.calls.append((to, message))


async def test_sms_channel_sends_to_all_recipients():
    provider = RecordingProvider()
    channel = SMSChannel(provider=provider, recipients=["+98911", "+98922"])
    await channel.send(_make_alert())
    assert len(provider.calls) == 2
    assert provider.calls[0][0] == "+98911"
    assert provider.calls[1][0] == "+98922"


async def test_sms_channel_message_contains_target_name():
    provider = RecordingProvider()
    channel = SMSChannel(provider=provider, recipients=["+98911"])
    await channel.send(_make_alert(target_name="MyServer"))
    _, message = provider.calls[0]
    assert "MyServer" in message


def test_build_channel_sms_returns_sms_channel():
    config = {
        "type": "sms",
        "provider": "kavenegar",
        "recipients": ["+98911"],
        "kavenegar": {"api_key": "fake-key", "sender": "1234"},
    }
    channel = build_channel(config)
    assert isinstance(channel, SMSChannel)


def test_build_channel_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown alert channel type"):
        build_channel({"type": "carrier_pigeon"})


def test_build_channel_telegram_returns_telegram_channel():
    config = {
        "type": "telegram",
        "chat_ids": ["111"],
        "telegram": {"bot_token": "fake-token"},
    }
    channel = build_channel(config)
    assert isinstance(channel, TelegramChannel)


@respx.mock
async def test_telegram_channel_sends_to_all_chat_ids():
    route = respx.post("https://api.telegram.org/botfake-token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    channel = TelegramChannel(bot_token="fake-token", chat_ids=["111", "222"])
    await channel.send(_make_alert(target_name="MyServer"))
    assert route.call_count == 2
    payloads = [json.loads(call.request.content) for call in route.calls]
    assert [p["chat_id"] for p in payloads] == ["111", "222"]
    assert "MyServer" in payloads[0]["text"]
    assert "message_thread_id" not in payloads[0]


@respx.mock
async def test_telegram_channel_sends_to_topic():
    route = respx.post("https://api.telegram.org/botfake-token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    channel = build_channel(
        {"type": "telegram", "chat_ids": ["-100123"], "telegram": {"bot_token": "fake-token", "topic_id": 586}}
    )
    await channel.send(_make_alert())
    payload = json.loads(route.calls[0].request.content)
    assert payload["chat_id"] == "-100123"
    assert payload["message_thread_id"] == 586
