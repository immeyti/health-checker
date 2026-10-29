from __future__ import annotations

import csv
import io
import json
import re
import secrets
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from ..config import target_host
from ..daemon import MonitoringDaemon
from ..models import ChannelIn, TargetIn
from ..state import StateTracker


_TEHRAN = ZoneInfo("Asia/Tehran")


def _parse_export_time(value: str | None, field: str) -> datetime | None:
    """Empty means no bound (blank form field); times without a timezone are Tehran time."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{field} must be a date and time like 2026-10-22T10:00")
    return dt.replace(tzinfo=_TEHRAN) if dt.tzinfo is None else dt


def _export_range(since: str | None, until: str | None) -> tuple[datetime | None, datetime | None]:
    since, until = _parse_export_time(since, "since"), _parse_export_time(until, "until")
    if since and until and since > until:
        raise HTTPException(422, "since must be before until")
    return since, until


def _csv_response(output: io.StringIO, target_name: str, kind: str) -> Response:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", target_name)
    return Response(
        output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={safe_name}-{kind}.csv"},
    )


def _csv_safe(value):
    """Prevent spreadsheet formulas in exported user-controlled text."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def build_router(
    state: StateTracker,
    daemon: MonitoringDaemon,
    templates: Jinja2Templates,
    retention_days: int,
    username: str | None,
    password: str | None,
    host_map: dict[str, str],
) -> APIRouter:
    _security = HTTPBasic()

    def verify_credentials(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
        correct_user = secrets.compare_digest(
            credentials.username.encode(), username.encode()
        )
        correct_pass = secrets.compare_digest(
            credentials.password.encode(), password.encode()
        )
        if not (correct_user and correct_pass):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    # Auth is off when no credentials are configured (e.g. local development)
    router = APIRouter(
        dependencies=[Depends(verify_credentials)] if username and password else []
    )

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, period: Literal["day", "week", "month"] = "day"):
        statuses = state.get_all_statuses(retention_days=retention_days, period=period)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "statuses": statuses,
                "retention_days": retention_days,
                "host_map": host_map,
                "period": period,
            },
        )

    @router.get("/history/{target_name}", response_class=HTMLResponse)
    async def target_history_page(request: Request, target_name: str):
        current = state.get_status(target_name, retention_days=retention_days)
        history = state.get_history(target_name, limit=100)
        attempts = state.get_attempts(target_name, limit=100)
        if current is None and not history and not attempts:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")

        groups: dict[str, dict] = {}
        for attempt in reversed(attempts):
            cycle_id = attempt["cycle_id"]
            if cycle_id not in groups:
                groups[cycle_id] = {
                    "status": attempt["status"],
                    "network_issue": attempt["network_issue"],
                    "attempts": [],
                }
            groups[cycle_id]["attempts"].append({
                **attempt,
                "checked_at_dt": datetime.fromisoformat(attempt["checked_at"]),
                "diagnostics": json.loads(attempt["diagnostics"]),
            })
        for group in groups.values():
            group["attempts"].reverse()
            group["recovered"] = (
                len(group["attempts"]) > 1
                and group["attempts"][0]["status"] == "DOWN"
                and group["status"] == "UP"
            )

        incidents = []
        down_start = None
        for log in reversed(history):
            if log.status == "DOWN" and down_start is None:
                down_start = log
            elif log.status == "UP" and down_start is not None:
                incidents.append({
                    "down": down_start,
                    "up": log,
                    "gap_seconds": round((log.checked_at - down_start.checked_at).total_seconds()),
                })
                down_start = None
        if down_start is not None:
            incidents.append({"down": down_start, "up": None, "gap_seconds": None})

        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "target_name": target_name,
                "current": current,
                "history": history,
                "groups": list(groups.values())[:30],
                "incidents": list(reversed(incidents))[:5],
                "down_count": sum(log.status == "DOWN" for log in history),
                "host": host_map.get(target_name, ""),
                "source_ip": daemon.public_ip,
            },
        )

    @router.get("/api/status")
    async def api_status():
        statuses = state.get_all_statuses(retention_days=retention_days)
        return JSONResponse(
            content=[s.model_dump(mode="json") for s in statuses]
        )

    @router.get("/api/status/{target_name}")
    async def api_status_single(target_name: str):
        status = state.get_status(target_name, retention_days=retention_days)
        if not status:
            return JSONResponse({"error": "Target not found"}, status_code=404)
        return JSONResponse(content=status.model_dump(mode="json"))

    @router.get("/api/history/{target_name}.csv")
    async def export_history(target_name: str, since: str | None = None,
                             until: str | None = None):
        since, until = _export_range(since, until)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(("checked_at_utc", "target_name", "target_type", "final_status",
                         "latency_ms", "error_message"))
        logs = state.get_history(target_name, limit=None, since=since, until=until)
        for log in reversed(logs):
            writer.writerow([_csv_safe(value) for value in (
                log.checked_at.isoformat(), log.target_name, log.target_type,
                log.status, log.latency_ms, log.error_message,
            )])
        return _csv_response(output, target_name, "outcomes")

    @router.get("/api/history/{target_name}")
    async def api_history(target_name: str, limit: int = 100, offset: int = 0):
        logs = state.get_history(target_name, limit=limit, offset=offset)
        return JSONResponse(
            content=[l.model_dump(mode="json") for l in logs]
        )

    @router.get("/api/attempts/{target_name}.csv")
    async def export_attempts(target_name: str, since: str | None = None,
                              until: str | None = None):
        since, until = _export_range(since, until)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(("checked_at_utc", "cycle_id", "attempt_number", "target_name",
                         "target_type", "status", "network_issue", "latency_ms",
                         "error_message", "source_ip", "server_ip", "resolved_ips", "http_status", "duration_ms", "exception_type", "request_host",
                         "response_host", "redirect_count", "http_version", "date",
                         "server", "cf_ray", "x_request_id"))

        for row in state.get_attempts(target_name, since=since, until=until):
            details = json.loads(row["diagnostics"])
            writer.writerow([_csv_safe(value) for value in (
                row["checked_at"], row["cycle_id"], row["attempt_number"],
                row["target_name"], row["target_type"], row["status"],
                row["network_issue"], row["latency_ms"], row["error_message"],
                details.get("source_ip"), details.get("server_ip"), details.get("resolved_ips"),
                details.get("http_status"), details.get("duration_ms"),
                details.get("exception_type"), details.get("request_host"),
                details.get("response_host"), details.get("redirect_count"),
                details.get("http_version"), details.get("date"), details.get("server"),
                details.get("cf_ray"), details.get("x_request_id"),
            )])
        return _csv_response(output, target_name, "attempts")

    # ------------------------------------------------------------------
    # Targets managed from the web UI
    # ------------------------------------------------------------------

    @router.get("/targets", response_class=HTMLResponse)
    async def targets_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="targets.html",
            context={"targets": state.get_targets(), "host_map": host_map},
        )

    @router.post("/api/targets", status_code=201)
    async def add_target(body: TargetIn):
        if daemon.has_target(body.name):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Target '{body.name}' already exists")
        target = body.to_target()
        state.add_target(target)
        daemon.add_target(target)
        host_map[target.name] = target_host(target)
        return {"name": target.name}

    @router.post("/api/targets/test")
    async def test_target(body: TargetIn):
        result = await daemon.check_once(body.to_target())
        return {
            "status": result.status,
            "latency_ms": result.latency_ms,
            "error_message": result.error_message,
        }

    @router.delete("/api/targets/{target_name}", status_code=204)
    async def delete_target(target_name: str):
        if not state.delete_target(target_name):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Target not found or defined in config.yaml"
            )
        await daemon.remove_target(target_name)
        host_map.pop(target_name, None)

    # ------------------------------------------------------------------
    # Alert channels managed from the web UI
    # ------------------------------------------------------------------

    @router.get("/channels", response_class=HTMLResponse)
    async def channels_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="channels.html",
            context={"channels": state.get_channels()},
        )

    @router.post("/api/channels", status_code=201)
    async def add_channel(body: ChannelIn):
        if daemon.has_channel(body.name):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Channel '{body.name}' already exists")
        daemon.add_channel(body.name, body.to_channel_config())
        state.add_channel(body.name, body.type, body.bot_token, body.chat_ids, body.topic_id)
        return {"name": body.name}

    @router.delete("/api/channels/{name}", status_code=204)
    async def delete_channel(name: str):
        if not state.delete_channel(name):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Channel not found or defined in config.yaml"
            )
        daemon.remove_channel(name)

    return router
