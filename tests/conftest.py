from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from src.config import (
    AlertsConfig,
    AppConfig,
    DashboardTarget,
    PingTarget,
    StorageConfig,
    SuccessIndicator,
    WebConfig,
)
from src.models import CheckResult
from src.state import StateTracker
from src.web.app import create_app

TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass"


# ---------------------------------------------------------------------------
# Module-level helper — call with keyword overrides inside any test
# ---------------------------------------------------------------------------

def make_result(
    name: str = "host-a",
    target_type: str = "ping",
    status: str = "UP",
    latency_ms: float = 12.5,
    checked_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    screenshot_path: Optional[str] = None,
) -> CheckResult:
    return CheckResult(
        target_name=name,
        target_type=target_type,
        status=status,
        latency_ms=latency_ms,
        checked_at=checked_at or datetime.now(timezone.utc),
        error_message=error_message,
        screenshot_path=screenshot_path,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state() -> StateTracker:
    tracker = StateTracker(db_path=":memory:")
    yield tracker
    tracker.close()


@pytest.fixture
def minimal_config() -> AppConfig:
    return AppConfig(
        check_interval=60,
        ping_targets=[PingTarget(name="loopback", host="127.0.0.1")],
        dashboard_targets=[],
        alerts=AlertsConfig(channels=[]),
        storage=StorageConfig(retention_days=30, screenshots_dir="screenshots"),
        web=WebConfig(host="127.0.0.1", port=8000),
    )


@pytest.fixture
def seeded_state(state: StateTracker) -> StateTracker:
    for _ in range(3):
        state.update(make_result(name="host-a", status="UP"))
    return state


def _make_app(state: StateTracker, tmp_path):
    os.environ["MONITOR_USERNAME"] = TEST_USERNAME
    os.environ["MONITOR_PASSWORD"] = TEST_PASSWORD
    try:
        return create_app(
            state=state,
            retention_days=30,
            screenshots_dir=str(tmp_path / "screenshots"),
        )
    finally:
        os.environ.pop("MONITOR_USERNAME", None)
        os.environ.pop("MONITOR_PASSWORD", None)


@pytest.fixture
def web_client(seeded_state: StateTracker, tmp_path) -> TestClient:
    app = _make_app(seeded_state, tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    client.auth = httpx.BasicAuth(TEST_USERNAME, TEST_PASSWORD)
    return client


@pytest.fixture
def unauthenticated_client(seeded_state: StateTracker, tmp_path) -> TestClient:
    app = _make_app(seeded_state, tmp_path)
    return TestClient(app, raise_server_exceptions=False)
