from __future__ import annotations

from .base import BaseChannel
from .sms.sms_channel import SMSChannel


def build_channel(config: dict) -> BaseChannel:
    """Factory: instantiate the correct channel from a config dict."""
    channel_type = config.get("type", "").lower()

    if channel_type == "sms":
        return SMSChannel.from_config(config)
    elif channel_type == "bale":
        from .bale.bale_channel import BaleChannel
        return BaleChannel.from_config(config)
    else:
        raise ValueError(f"Unknown alert channel type: {channel_type!r}")


__all__ = ["BaseChannel", "build_channel"]
