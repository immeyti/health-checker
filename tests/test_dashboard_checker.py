from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from src.checkers.dashboard import DashboardChecker
from src.config import DashboardTarget, SuccessIndicator


def _make_target(name: str = "my-dashboard") -> DashboardTarget:
    return DashboardTarget(
        name=name,
        url="https://example.com/login",
        username="admin",
        password="secret",
        success_indicator=SuccessIndicator(type="url_contains", value="/dashboard"),
        timeout=5000,
    )


class SuccessfulDashboardChecker(DashboardChecker):
    async def _do_check(self) -> tuple[bool, Optional[str], Optional[str]]:
        return True, None, None


class FailingDashboardChecker(DashboardChecker):
    async def _do_check(self) -> tuple[bool, Optional[str], Optional[str]]:
        return False, None, "Login failed: could not find username field"


async def test_dashboard_checker_up():
    checker = SuccessfulDashboardChecker(_make_target())
    result = await checker.check()
    assert result.status == "UP"
    assert result.target_name == "my-dashboard"
    assert result.target_type == "dashboard"
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    assert result.error_message is None


async def test_dashboard_checker_down_with_error():
    checker = FailingDashboardChecker(_make_target())
    result = await checker.check()
    assert result.status == "DOWN"
    assert result.error_message == "Login failed: could not find username field"
    assert result.target_type == "dashboard"


# ---------------------------------------------------------------------------
# Video retention logic (_handle_recording)
# ---------------------------------------------------------------------------


async def test_video_discarded_on_success(tmp_path):
    """Successful check must delete the tmp video and return None."""
    checker = DashboardChecker(_make_target(), screenshots_dir=str(tmp_path / "screenshots"))
    checker._videos_dir = tmp_path  # point directly at tmp_path for simplicity

    # Create a fake tmp video file (simulates what Playwright produces)
    fake_video = tmp_path / "uuid-abc123.webm"
    fake_video.write_bytes(b"fake")

    result = await checker._handle_recording(success=True, tmp_video_path=str(fake_video))

    assert result is None
    assert not fake_video.exists(), "tmp video must be deleted on success"


async def test_video_kept_on_failure(tmp_path):
    """Failed check must rename the tmp video to the stable per-target filename."""
    checker = DashboardChecker(_make_target(), screenshots_dir=str(tmp_path / "screenshots"))
    checker._videos_dir = tmp_path

    fake_video = tmp_path / "uuid-abc123.webm"
    fake_video.write_bytes(b"fake")

    result = await checker._handle_recording(success=False, tmp_video_path=str(fake_video))

    assert result is not None
    stable = Path(result)
    assert stable.exists(), "renamed video must exist on failure"
    assert stable.name == "my-dashboard.webm"
    assert not fake_video.exists(), "original tmp file should be gone after rename"


async def test_video_handle_none_path(tmp_path):
    """No tmp path (browser crash before recording started) must not raise."""
    checker = DashboardChecker(_make_target(), screenshots_dir=str(tmp_path / "screenshots"))
    checker._videos_dir = tmp_path

    result_success = await checker._handle_recording(success=True, tmp_video_path=None)
    result_failure = await checker._handle_recording(success=False, tmp_video_path=None)

    assert result_success is None
    assert result_failure is None
