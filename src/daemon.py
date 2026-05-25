from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .channels import BaseChannel, build_channel
from .checkers.base import BaseChecker
from .checkers.dashboard import DashboardChecker
from .checkers.ping import PingChecker
from .checkers.tcp import TCPChecker
from .config import AppConfig
from .models import Alert
from .state import StateTracker

logger = logging.getLogger(__name__)

# Limit concurrent Playwright browser contexts
_PLAYWRIGHT_SEMAPHORE = asyncio.Semaphore(3)


class MonitoringDaemon:
    def __init__(self, config: AppConfig, state: StateTracker) -> None:
        self._config = config
        self._state = state
        self._checkers: list[BaseChecker] = self._build_checkers()
        self._channels: list[BaseChannel] = self._build_channels()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        logger.info(
            "Daemon started — %d checker(s), interval=%ds",
            len(self._checkers),
            self._config.check_interval,
        )
        try:
            while self._running:
                next_run = time.monotonic() + self._config.check_interval
                await self._run_cycle()
                sleep_for = max(0.0, next_run - time.monotonic())
                logger.debug("Next cycle in %.1fs", sleep_for)
                await asyncio.sleep(sleep_for)
        finally:
            await self._shutdown()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_cycle(self) -> None:
        logger.debug("Starting check cycle for %d targets", len(self._checkers))
        self._state.purge_old_logs(self._config.storage.retention_days)

        tasks = [self._check_target(checker) for checker in self._checkers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_target(self, checker: BaseChecker) -> None:
        try:
            if checker.target_type == "dashboard":
                async with _PLAYWRIGHT_SEMAPHORE:
                    result = await checker.check()
            else:
                result = await checker.check()

            alert: Optional[Alert] = self._state.update(result)
            if alert:
                await self._dispatch_alert(alert)
        except Exception as exc:
            logger.error(
                "Unexpected error checking %s: %s", checker.name, exc, exc_info=True
            )

    async def _dispatch_alert(self, alert: Alert) -> None:
        if not self._channels:
            logger.warning("Alert triggered but no channels configured: %s", alert.message)
            return
        tasks = [ch.send(alert) for ch in self._channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ch, res in zip(self._channels, results):
            if isinstance(res, Exception):
                logger.error("Channel %s failed to send alert: %s", type(ch).__name__, res)

    def _build_checkers(self) -> list[BaseChecker]:
        checkers: list[BaseChecker] = []
        for t in self._config.ping_targets:
            checkers.append(PingChecker(t))
        for t in self._config.tcp_targets:
            checkers.append(TCPChecker(t))
        for t in self._config.dashboard_targets:
            checkers.append(DashboardChecker(t, screenshots_dir=self._config.storage.screenshots_dir))
        return checkers

    def _build_channels(self) -> list[BaseChannel]:
        channels: list[BaseChannel] = []
        for ch_cfg in self._config.alerts.channels:
            try:
                channels.append(build_channel(ch_cfg.raw))
            except Exception as exc:
                logger.error("Failed to build channel %s: %s", ch_cfg.type, exc)
        return channels

    async def _shutdown(self) -> None:
        logger.info("Daemon shutting down…")
        for checker in self._checkers:
            if isinstance(checker, DashboardChecker):
                await checker.close()
        self._state.close()
        logger.info("Daemon stopped.")
