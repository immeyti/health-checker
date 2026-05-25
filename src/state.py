from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Alert, CheckLog, CheckResult, TargetStatus

logger = logging.getLogger(__name__)

_CREATE_STATES_TABLE = """
CREATE TABLE IF NOT EXISTS target_states (
    name                TEXT PRIMARY KEY,
    target_type         TEXT NOT NULL,
    current_status      TEXT NOT NULL,
    previous_status     TEXT,
    last_checked        TEXT NOT NULL,
    latency_ms          REAL,
    consecutive_failures INTEGER DEFAULT 0,
    first_seen          TEXT NOT NULL,
    screenshot_path     TEXT
);
"""

_CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS check_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name   TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    status        TEXT NOT NULL,
    latency_ms    REAL,
    checked_at    TEXT NOT NULL,
    error_message TEXT,
    screenshot_path TEXT
);
"""

_CREATE_LOGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_check_logs_target_time
ON check_logs (target_name, checked_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class StateTracker:
    def __init__(self, db_path: str = "monitor.db") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_STATES_TABLE)
            self._conn.execute(_CREATE_LOGS_TABLE)
            self._conn.execute(_CREATE_LOGS_INDEX)
        self._migrate_db()
        logger.info("Database initialised at %s", self._db_path)

    def _migrate_db(self) -> None:
        """Add columns introduced after the initial schema — safe to run on every startup."""
        migrations = [
            ("check_logs", "screenshot_path", "TEXT"),
            ("target_states", "screenshot_path", "TEXT"),
        ]
        for table, column, definition in migrations:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                self._conn.commit()
                logger.info("Migration: added %s.%s", table, column)
            except sqlite3.OperationalError:
                pass  # Column already exists

    # ------------------------------------------------------------------
    # Core update — called after every check
    # ------------------------------------------------------------------

    def update(self, result: CheckResult) -> Optional[Alert]:
        now_iso = result.checked_at.isoformat()

        # 1. Append to check_logs
        self._conn.execute(
            """
            INSERT INTO check_logs
                (target_name, target_type, status, latency_ms, checked_at, error_message, screenshot_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.target_name,
                result.target_type,
                result.status,
                result.latency_ms,
                now_iso,
                result.error_message,
                result.screenshot_path,
            ),
        )
        self._conn.commit()

        # 2. Read previous state
        row = self._conn.execute(
            "SELECT * FROM target_states WHERE name = ?", (result.target_name,)
        ).fetchone()

        previous_status: Optional[str] = None
        consecutive_failures = 0
        first_seen = now_iso
        alert: Optional[Alert] = None

        if row:
            previous_status = row["current_status"]
            first_seen = row["first_seen"]
            consecutive_failures = row["consecutive_failures"]

            if result.status == "DOWN":
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            # Only alert on transition; never alert on very first check (row existed already)
            if previous_status != result.status:
                transition = (
                    "UP_TO_DOWN" if result.status == "DOWN" else "DOWN_TO_UP"
                )
                msg = (
                    f"[{transition}] {result.target_name} ({result.target_type}) "
                    f"is now {result.status}"
                )
                if result.error_message:
                    msg += f" — {result.error_message}"
                alert = Alert(
                    target_name=result.target_name,
                    target_type=result.target_type,
                    transition=transition,
                    triggered_at=result.checked_at,
                    message=msg,
                )
                logger.warning("Transition: %s", msg)
        else:
            # First ever check — insert without alert
            if result.status == "DOWN":
                consecutive_failures = 1
            logger.info(
                "First check for %s → %s", result.target_name, result.status
            )

        # 3. Upsert current state
        self._conn.execute(
            """
            INSERT OR REPLACE INTO target_states
                (name, target_type, current_status, previous_status,
                 last_checked, latency_ms, consecutive_failures, first_seen, screenshot_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.target_name,
                result.target_type,
                result.status,
                previous_status,
                now_iso,
                result.latency_ms,
                consecutive_failures,
                first_seen,
                result.screenshot_path,
            ),
        )
        self._conn.commit()
        return alert

    # ------------------------------------------------------------------
    # Queries for the web dashboard
    # ------------------------------------------------------------------

    def get_all_statuses(self, retention_days: int = 30) -> list[TargetStatus]:
        rows = self._conn.execute(
            "SELECT * FROM target_states ORDER BY name"
        ).fetchall()
        result = []
        for row in rows:
            ts = self._row_to_status(row, retention_days)
            result.append(ts)
        return result

    def get_status(self, target_name: str, retention_days: int = 30) -> Optional[TargetStatus]:
        row = self._conn.execute(
            "SELECT * FROM target_states WHERE name = ?", (target_name,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_status(row, retention_days)

    def get_history(
        self,
        target_name: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CheckLog]:
        rows = self._conn.execute(
            """
            SELECT * FROM check_logs
            WHERE target_name = ?
            ORDER BY checked_at DESC
            LIMIT ? OFFSET ?
            """,
            (target_name, limit, offset),
        ).fetchall()
        return [
            CheckLog(
                id=r["id"],
                target_name=r["target_name"],
                target_type=r["target_type"],
                status=r["status"],
                latency_ms=r["latency_ms"],
                checked_at=_parse_dt(r["checked_at"]),
                error_message=r["error_message"],
                screenshot_path=r["screenshot_path"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_old_logs(self, retention_days: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        cur = self._conn.execute(
            "DELETE FROM check_logs WHERE checked_at < ?", (cutoff,)
        )
        self._conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("Purged %d old log entries (older than %d days)", deleted, retention_days)
        return deleted

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _row_to_status(self, row: sqlite3.Row, retention_days: int) -> TargetStatus:
        ts = TargetStatus(
            name=row["name"],
            target_type=row["target_type"],
            current_status=row["current_status"],
            previous_status=row["previous_status"],
            last_checked=_parse_dt(row["last_checked"]),
            latency_ms=row["latency_ms"],
            consecutive_failures=row["consecutive_failures"],
            first_seen=_parse_dt(row["first_seen"]),
            screenshot_path=row["screenshot_path"],
        )
        ts.uptime_pct = self._calc_uptime(row["name"], retention_days)
        ts.avg_latency_ms = self._calc_avg_latency(row["name"], retention_days)
        ts.timeline_24h = self._calc_timeline_24h(row["name"])
        return ts

    def _calc_uptime(self, target_name: str, retention_days: int) -> Optional[float]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'UP' THEN 1 ELSE 0 END) AS up_count
            FROM check_logs
            WHERE target_name = ? AND checked_at >= ?
            """,
            (target_name, cutoff),
        ).fetchone()
        if not row or not row["total"]:
            return None
        return round(row["up_count"] / row["total"] * 100, 2)

    def _calc_avg_latency(self, target_name: str, retention_days: int) -> Optional[float]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        row = self._conn.execute(
            """
            SELECT AVG(latency_ms) AS avg_lat
            FROM check_logs
            WHERE target_name = ? AND checked_at >= ? AND latency_ms IS NOT NULL
            """,
            (target_name, cutoff),
        ).fetchone()
        if not row or row["avg_lat"] is None:
            return None
        return round(row["avg_lat"], 2)

    def _calc_timeline_24h(self, target_name: str) -> list[str]:
        """Return list of 24 hourly bucket statuses (oldest→newest).
        Each bucket is 'UP', 'DOWN', or 'UNKNOWN' if no data."""
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=24)).isoformat()

        rows = self._conn.execute(
            """
            SELECT
                strftime('%Y-%m-%dT%H', checked_at) AS hour_bucket,
                SUM(CASE WHEN status = 'UP' THEN 1 ELSE 0 END) AS up_count,
                COUNT(*) AS total
            FROM check_logs
            WHERE target_name = ? AND checked_at >= ?
            GROUP BY hour_bucket
            ORDER BY hour_bucket ASC
            """,
            (target_name, cutoff),
        ).fetchall()

        # Build a map: hour_str -> status
        bucket_map: dict[str, str] = {}
        for r in rows:
            if r["total"] == 0:
                bucket_map[r["hour_bucket"]] = "UNKNOWN"
            elif r["up_count"] == r["total"]:
                bucket_map[r["hour_bucket"]] = "UP"
            elif r["up_count"] == 0:
                bucket_map[r["hour_bucket"]] = "DOWN"
            else:
                bucket_map[r["hour_bucket"]] = "DOWN"  # partial DOWN counts as DOWN

        timeline = []
        for i in range(24):
            hour = now - timedelta(hours=23 - i)
            key = hour.strftime("%Y-%m-%dT%H")
            timeline.append(bucket_map.get(key, "UNKNOWN"))
        return timeline
