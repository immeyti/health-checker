from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from .config import DashboardTarget, HttpTarget, PingTarget, SuccessIndicator, TcpTarget


class CheckResult(BaseModel):
    target_name: str
    target_type: Literal["ping", "dashboard", "tcp", "http"]
    status: Literal["UP", "DOWN"]
    latency_ms: Optional[float] = None
    checked_at: datetime
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None
    diagnostics: dict[str, str | int | float | None] = Field(default_factory=dict)


class TargetStatus(BaseModel):
    name: str
    target_type: str
    current_status: str
    previous_status: Optional[str] = None
    last_checked: datetime
    latency_ms: Optional[float] = None
    consecutive_failures: int = 0
    first_seen: datetime

    # Computed fields populated by StateTracker
    uptime_pct: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    timeline_24h: list[str] = []  # list of 24 hourly buckets: "UP", "DOWN", or "UNKNOWN"
    screenshot_path: Optional[str] = None


class Alert(BaseModel):
    target_name: str
    target_type: str
    transition: Literal["UP_TO_DOWN", "DOWN_TO_UP"]
    triggered_at: datetime
    message: str


class CheckLog(BaseModel):
    id: Optional[int] = None
    target_name: str
    target_type: str
    status: str
    latency_ms: Optional[float] = None
    checked_at: datetime
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Web UI input for adding / testing a target
# ---------------------------------------------------------------------------


class PingTargetIn(BaseModel):
    type: Literal["ping"]
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)

    def to_target(self) -> PingTarget:
        return PingTarget(name=self.name, host=self.host)


class TcpTargetIn(BaseModel):
    type: Literal["tcp"]
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    timeout: int = Field(default=5000, gt=0)

    def to_target(self) -> TcpTarget:
        return TcpTarget(name=self.name, host=self.host, port=self.port, timeout=self.timeout)


class DashboardTargetIn(BaseModel):
    type: Literal["dashboard"]
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    indicator_type: Literal["url_contains", "element_exists", "text_contains"]
    indicator_value: str = Field(min_length=1)
    timeout: int = Field(default=15000, gt=0)

    def to_target(self) -> DashboardTarget:
        return DashboardTarget(
            name=self.name,
            url=self.url,
            username=self.username,
            password=self.password,
            success_indicator=SuccessIndicator(type=self.indicator_type, value=self.indicator_value),
            timeout=self.timeout,
        )


class HttpTargetIn(BaseModel):
    type: Literal["http"]
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    expected_text: str = ""
    forbidden_text: str = ""
    timeout: int = Field(default=10000, gt=0)

    def to_target(self) -> HttpTarget:
        return HttpTarget(
            name=self.name,
            url=self.url,
            expected_text=self.expected_text,
            forbidden_text=self.forbidden_text,
            timeout=self.timeout,
        )


TargetIn = Annotated[
    PingTargetIn | TcpTargetIn | DashboardTargetIn | HttpTargetIn, Field(discriminator="type")
]


# ---------------------------------------------------------------------------
# Web UI input for adding an alert channel
# ---------------------------------------------------------------------------


class ChannelIn(BaseModel):
    type: Literal["telegram", "bale"]
    name: str = Field(min_length=1)
    bot_token: str = Field(min_length=1)
    chat_ids: list[str] = Field(min_length=1)
    topic_id: Optional[int] = None  # Telegram forum topic; ignored by Bale

    def to_channel_config(self) -> dict:
        return {
            "type": self.type,
            "chat_ids": self.chat_ids,
            self.type: {"bot_token": self.bot_token, "topic_id": self.topic_id},
        }
