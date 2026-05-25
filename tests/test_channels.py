from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.channels import build_channel
from src.channels.sms.base_provider import BaseSMSProvider
from src.channels.sms.sms_channel import SMSChannel
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
        build_channel({"type": "telegram"})
