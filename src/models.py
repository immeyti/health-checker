from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints

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
    timeline: list[str] = []  # buckets for the selected period: "UP", "DOWN", or "UNKNOWN"
    screenshot_path: Optional[str] = None
    paused: bool = False


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


# A "/" can't be addressed by the /api/targets/{name} routes
TargetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^/]+$")]
# Results are recorded after the whole cycle, so one long timeout delays every target's alerts
TimeoutMs = Annotated[int, Field(gt=0, le=120_000)]
# A blank form field means no limit
MaxLatency = Annotated[Optional[Annotated[int, Field(gt=0)]], BeforeValidator(lambda v: None if v == "" else v)]


class PingTargetIn(BaseModel):
    type: Literal["ping"]
    name: TargetName
    host: str = Field(pattern=r"^[^-\s]\S*$")  # a leading "-" would be read as a ping option
    timeout: TimeoutMs = 3000
    max_latency: MaxLatency = None

    def to_target(self) -> PingTarget:
        return PingTarget(name=self.name, host=self.host, timeout=self.timeout, max_latency=self.max_latency)


class TcpTargetIn(BaseModel):
    type: Literal["tcp"]
    name: TargetName
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    timeout: TimeoutMs = 5000
    max_latency: MaxLatency = None

    def to_target(self) -> TcpTarget:
        return TcpTarget(
            name=self.name, host=self.host, port=self.port, timeout=self.timeout, max_latency=self.max_latency
        )


class DashboardTargetIn(BaseModel):
    type: Literal["dashboard"]
    name: TargetName
    url: str = Field(pattern=r"^https?://")  # the browser would open file:// too
    username: str = Field(min_length=1)
    password: str = ""  # blank keeps the stored password when editing; routes enforce it otherwise
    indicator_type: Literal["url_contains", "element_exists", "text_contains"]
    indicator_value: str = Field(min_length=1)
    timeout: TimeoutMs = 15000
    max_latency: MaxLatency = None

    def to_target(self) -> DashboardTarget:
        return DashboardTarget(
            name=self.name,
            url=self.url,
            username=self.username,
            password=self.password,
            success_indicator=SuccessIndicator(type=self.indicator_type, value=self.indicator_value),
            timeout=self.timeout,
            max_latency=self.max_latency,
        )


class HttpTargetIn(BaseModel):
    type: Literal["http"]
    name: TargetName
    url: str = Field(pattern=r"^https?://")
    expected_text: str = ""
    forbidden_text: str = ""
    timeout: TimeoutMs = 10000
    max_latency: MaxLatency = None

    def to_target(self) -> HttpTarget:
        return HttpTarget(
            name=self.name,
            url=self.url,
            expected_text=self.expected_text,
            forbidden_text=self.forbidden_text,
            timeout=self.timeout,
            max_latency=self.max_latency,
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
