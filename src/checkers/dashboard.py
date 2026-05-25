from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from ..config import DashboardTarget, SuccessIndicator
from ..models import CheckResult
from .base import BaseChecker

logger = logging.getLogger(__name__)

VIDEO_SIZE = {"width": 1280, "height": 720}
# Delay between each browser action (ms) — makes video readable
SLOW_MO_MS = 300
# Pause after filling each field before moving to the next (seconds)
STEP_PAUSE = 0.6

# Priority-ordered selectors for login form fields
_USERNAME_SELECTORS = [
    "input[name='username']",
    "input[name='email']",
    "input[type='email']",
    "#username",
    "#email",
    "input[autocomplete='username']",
]
_PASSWORD_SELECTORS = [
    "input[name='password']",
    "input[type='password']",
    "#password",
    "input[autocomplete='current-password']",
]
_SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Login')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "[data-testid='login-button']",
]


class DashboardChecker(BaseChecker):
    def __init__(self, target: DashboardTarget, screenshots_dir: str = "screenshots") -> None:
        self._target = target
        # videos are stored alongside screenshots dir
        self._videos_dir = Path(screenshots_dir).parent / "videos"
        self._browser: Optional[Browser] = None
        self._playwright = None

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def target_type(self):
        return "dashboard"

    async def check(self) -> CheckResult:
        checked_at = datetime.now(timezone.utc)
        start = time.monotonic()

        success, video_path, error_msg = await self._do_check()

        elapsed_ms = (time.monotonic() - start) * 1000
        status = "UP" if success else "DOWN"
        logger.info("DASH %s → %s %.1f ms", self._target.name, status, elapsed_ms)
        return CheckResult(
            target_name=self._target.name,
            target_type="dashboard",
            status=status,
            latency_ms=round(elapsed_ms, 2),
            checked_at=checked_at,
            error_message=error_msg,
            screenshot_path=video_path,  # reuse field for video path
        )

    async def _do_check(self) -> tuple[bool, Optional[str], Optional[str]]:
        """Returns (success, video_path, error_message)."""
        try:
            browser = await self._get_browser()
        except Exception as exc:
            logger.warning("DASH %s — browser launch failed: %s", self._target.name, exc)
            await self._close_browser()
            return False, None, f"Browser launch failed: {str(exc)[:150]}"

        self._videos_dir.mkdir(parents=True, exist_ok=True)

        context = await browser.new_context(
            record_video_dir=str(self._videos_dir),
            record_video_size=VIDEO_SIZE,
        )
        page = None
        tmp_video_path: Optional[str] = None
        success = False
        error_msg: Optional[str] = None
        try:
            page = await context.new_page()
            timeout = self._target.timeout

            await page.goto(self._target.url, timeout=timeout, wait_until="domcontentloaded")
            # Pause so the video clearly shows the loaded login page
            await asyncio.sleep(STEP_PAUSE)

            username_sel = await self._find_selector(page, _USERNAME_SELECTORS)
            if not username_sel:
                raise RuntimeError("Could not find username field on login page")

            password_sel = await self._find_selector(page, _PASSWORD_SELECTORS)
            if not password_sel:
                raise RuntimeError("Could not find password field on login page")

            # Click field first so it's visible in the video, then type
            await page.click(username_sel, timeout=timeout)
            await page.fill(username_sel, self._target.username, timeout=timeout)
            await asyncio.sleep(STEP_PAUSE)

            await page.click(password_sel, timeout=timeout)
            await page.fill(password_sel, self._target.password, timeout=timeout)
            await asyncio.sleep(STEP_PAUSE)

            url_before = page.url
            submit_sel = await self._find_selector(page, _SUBMIT_SELECTORS)
            if submit_sel:
                await page.click(submit_sel, timeout=timeout)
            else:
                await page.press(password_sel, "Enter")

            # Wait for URL to change after submit — fixes timing race with networkidle
            try:
                await page.wait_for_url(
                    lambda url: url != url_before,
                    timeout=timeout,
                )
            except PlaywrightTimeout:
                pass  # some dashboards don't change URL; proceed to verify

            # Extra settle for heavy dashboards
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeout:
                pass

            success = await self._verify_success(page, self._target.success_indicator, timeout)
            # Hold on the result page so the video shows the final state clearly
            await asyncio.sleep(STEP_PAUSE * 2)
            error_msg = None if success else f"Login verification failed (landed on: {page.url})"

        except Exception as exc:
            error_msg = str(exc)[:200]

        finally:
            # Must get video path BEFORE context.close() finalises the video
            if page is not None and page.video is not None:
                try:
                    tmp_video_path = await page.video.path()
                except Exception:
                    pass
            await context.close()

        video_path = await self._handle_recording(success, tmp_video_path)
        return success, video_path, error_msg

    async def _handle_recording(self, success: bool, tmp_video_path: Optional[str]) -> Optional[str]:
        """Discard the video on success; keep and rename it on failure."""
        if success:
            if tmp_video_path:
                try:
                    Path(tmp_video_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return None
        return await self._finalize_video(tmp_video_path)

    async def _finalize_video(self, tmp_path: Optional[str]) -> Optional[str]:
        """Rename Playwright's UUID video file to a stable per-target filename."""
        if not tmp_path:
            return None
        try:
            src = Path(tmp_path)
            if not src.exists():
                return None
            safe_name = re.sub(r"[^\w\-]", "_", self._target.name)
            dst = self._videos_dir / f"{safe_name}.webm"
            src.replace(dst)
            logger.debug("Video saved: %s", dst)
            return dst.as_posix()
        except Exception as exc:
            logger.warning("Video finalize failed for %s: %s", self._target.name, exc)
            return None

    async def _verify_success(
        self, page, indicator: SuccessIndicator, timeout: int
    ) -> bool:
        try:
            if indicator.type == "url_contains":
                return indicator.value in page.url

            elif indicator.type == "element_exists":
                await page.wait_for_selector(indicator.value, timeout=timeout)
                return await page.locator(indicator.value).count() > 0

            elif indicator.type == "text_contains":
                body_text = await page.locator("body").inner_text()
                return indicator.value in body_text

            else:
                logger.warning("Unknown success_indicator type: %s", indicator.type)
                return False

        except PlaywrightTimeout:
            return False

    async def _find_selector(self, page, selectors: list[str]) -> Optional[str]:
        for sel in selectors:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    return sel
            except PlaywrightError:
                continue
        return None

    async def _get_browser(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            slow_mo=SLOW_MO_MS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self._browser

    async def _close_browser(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None

    async def close(self) -> None:
        await self._close_browser()
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None
