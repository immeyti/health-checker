from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from .channels import BaseChannel, build_channel
from .checkers.base import BaseChecker
from .checkers.dashboard import DashboardChecker
from .checkers.http import HTTPChecker
from .checkers.ping import PingChecker
from .checkers.tcp import TCPChecker
from .config import AppConfig, DashboardTarget, HttpTarget, Target, TcpTarget
from .connectivity import ConnectivityProbe, PublicIPLookup
from .models import Alert, CheckResult
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
        self._ui_channels: dict[str, BaseChannel] = self._build_ui_channels()
        self._connectivity = ConnectivityProbe(config.connectivity_hosts)
        self._public_ip = PublicIPLookup(config.public_ip_url)
        self._network_down = False
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

    @property
    def public_ip(self) -> Optional[str]:
        return self._public_ip.value

    def stop(self) -> None:
        self._running = False

    def has_target(self, name: str) -> bool:
        return any(c.name == name for c in self._checkers)

    def add_target(self, target: Target) -> None:
        self._checkers.append(self._make_checker(target))

    async def remove_target(self, name: str) -> None:
        checker = next(c for c in self._checkers if c.name == name)
        self._checkers.remove(checker)
        if isinstance(checker, DashboardChecker):
            await checker.close()

    def has_channel(self, name: str) -> bool:
        return name in self._ui_channels

    def add_channel(self, name: str, config: dict) -> None:
        self._ui_channels[name] = build_channel(config)

    def remove_channel(self, name: str) -> None:
        self._ui_channels.pop(name, None)

    async def check_once(self, target: Target) -> CheckResult:
        """Run a single check without recording it or sending alerts."""
        checker = self._make_checker(target)
        if not isinstance(checker, DashboardChecker):
            return await checker.check()
        try:
            async with _PLAYWRIGHT_SEMAPHORE:
                return await checker.check()
        finally:
            await checker.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_cycle(self) -> None:
        logger.debug("Starting check cycle for %d targets", len(self._checkers))
        self._state.purge_old_logs(self._config.storage.retention_days)

        await self._public_ip.refresh()
        checkers = list(self._checkers)
        outcomes = await asyncio.gather(*(self._check_target(c) for c in checkers))
        # Skip failed checks and targets deleted while their check was running
        outcomes = [o for c, o in zip(checkers, outcomes) if o and c in self._checkers]
        finished = [result for _, result in outcomes]

        down_cycles = [cycle_id for cycle_id, result in outcomes if result.status == "DOWN"]
        reason = issue = None
        if len(finished) >= 2 and len(down_cycles) == len(finished):
            reason = f"All {len(finished)} targets failed at the same time."
            issue = "all_targets_failed"
        elif down_cycles and not await self._connectivity.is_online():
            reason = "Reference hosts are unreachable from the monitor."
            issue = "monitor_offline"

        if reason:
            # The monitor's own connection is the likely cause: don't record
            # DOWN or alert per target; send one network alert instead.
            logger.warning("Monitor network problem — %s Site DOWN results not recorded.", reason)
            self._state.mark_network_issue(down_cycles, issue)
            finished = [r for r in finished if r.status == "UP"]
            if not self._network_down:
                self._network_down = True
                await self._dispatch_alert(self._network_alert("UP_TO_DOWN", reason))
        elif self._network_down:
            self._network_down = False
            logger.info("Monitor network restored")
            await self._dispatch_alert(self._network_alert(
                "DOWN_TO_UP", "Site checks are running normally again."))

        for result in finished:
            alert: Optional[Alert] = self._state.update(result)
            if alert:
                await self._dispatch_alert(alert)

    @staticmethod
    def _network_alert(transition: str, message: str) -> Alert:
        return Alert(
            target_name="Monitor network",
            target_type="network",
            transition=transition,
            triggered_at=datetime.now(timezone.utc),
            message=message,
        )

    async def _check_target(self, checker: BaseChecker) -> Optional[tuple[str, CheckResult]]:
        """Check one target, retrying a DOWN result; (cycle_id, final result), or None if it crashed."""
        try:
            cycle_id = uuid4().hex
            result = await self._check_and_record(checker, cycle_id, 1)
            retries = (
                self._config.dashboard_retries
                if checker.target_type == "dashboard"
                else self._config.retries
            )
            for attempt in range(1, retries + 1):
                if result.status == "UP" or checker not in self._checkers:
                    break
                logger.info(
                    "Retry %d/%d for %s after DOWN (%s)",
                    attempt, retries, checker.name, result.error_message,
                )
                await asyncio.sleep(self._config.retry_delay)
                result = await self._check_and_record(checker, cycle_id, attempt + 1)
            return cycle_id, result
        except Exception as exc:
            logger.error(
                "Unexpected error checking %s: %s", checker.name, exc, exc_info=True
            )
            return None

    async def _check_and_record(self, checker: BaseChecker, cycle_id: str,
                                attempt_number: int) -> CheckResult:
        try:
            result = await self._run_check(checker)
        except Exception as exc:
            logger.exception("Check attempt failed unexpectedly for %s", checker.name)
            result = CheckResult(
                target_name=checker.name,
                target_type=checker.target_type,
                status="DOWN",
                checked_at=datetime.now(timezone.utc),
                error_message=f"{type(exc).__name__}: {str(exc)[:200]}",
                diagnostics={"exception_type": type(exc).__name__},
            )
        if self._public_ip.value:
            result.diagnostics["source_ip"] = self._public_ip.value
        self._state.record_attempt(result, cycle_id, attempt_number)
        return result

    async def _run_check(self, checker: BaseChecker) -> CheckResult:
        if checker.target_type == "dashboard":
            async with _PLAYWRIGHT_SEMAPHORE:
                return await checker.check()
        return await checker.check()

    async def _dispatch_alert(self, alert: Alert) -> None:
        channels = [*self._channels, *self._ui_channels.values()]
        if not channels:
            logger.warning("Alert triggered but no channels configured: %s", alert.message)
            return
        tasks = [ch.send(alert) for ch in channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ch, res in zip(channels, results):
            if isinstance(res, Exception):
                logger.error("Channel %s failed to send alert: %s", type(ch).__name__, res)

    def _build_checkers(self) -> list[BaseChecker]:
        c = self._config
        targets = [
            *c.ping_targets,
            *c.tcp_targets,
            *c.dashboard_targets,
            *c.http_targets,
            *self._state.get_targets(),
        ]
        return [self._make_checker(t) for t in targets]

    def _make_checker(self, target: Target) -> BaseChecker:
        if isinstance(target, DashboardTarget):
            return DashboardChecker(target, screenshots_dir=self._config.storage.screenshots_dir)
        if isinstance(target, TcpTarget):
            return TCPChecker(target)
        if isinstance(target, HttpTarget):
            return HTTPChecker(target)
        return PingChecker(target)

    def _build_channels(self) -> list[BaseChannel]:
        channels: list[BaseChannel] = []
        for ch_cfg in self._config.alerts.channels:
            try:
                channels.append(build_channel(ch_cfg.raw))
            except Exception as exc:
                logger.error("Failed to build channel %s: %s", ch_cfg.type, exc)
        return channels

    def _build_ui_channels(self) -> dict[str, BaseChannel]:
        channels: dict[str, BaseChannel] = {}
        for c in self._state.get_channels():
            cfg = {
                "type": c["type"],
                "chat_ids": c["chat_ids"],
                c["type"]: {"bot_token": c["bot_token"], "topic_id": c["topic_id"]},
            }
            try:
                channels[c["name"]] = build_channel(cfg)
            except Exception as exc:
                logger.error("Failed to build channel %s: %s", c["name"], exc)
        return channels

    async def _shutdown(self) -> None:
        logger.info("Daemon shutting down…")
        for checker in self._checkers:
            if isinstance(checker, DashboardChecker):
                await checker.close()
        self._state.close()
        logger.info("Daemon stopped.")
