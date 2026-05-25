from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class CheckResult(BaseModel):
    target_name: str
    target_type: Literal["ping", "dashboard", "tcp"]
    status: Literal["UP", "DOWN"]
    latency_ms: Optional[float] = None
    checked_at: datetime
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


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
