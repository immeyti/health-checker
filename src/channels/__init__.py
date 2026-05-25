from __future__ import annotations

from .base import BaseChannel
from .sms.sms_channel import SMSChannel


def build_channel(config: dict) -> BaseChannel:
    """Factory: instantiate the correct channel from a config dict."""
    channel_type = config.get("type", "").lower()

    if channel_type == "sms":
        return SMSChannel.from_config(config)
    # Future channels:
    # elif channel_type == "telegram":
    #     from .telegram.telegram_channel import TelegramChannel
    #     return TelegramChannel.from_config(config)
    else:
        raise ValueError(f"Unknown alert channel type: {channel_type!r}")


__all__ = ["BaseChannel", "build_channel"]
