from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from ..config import target_host
from ..daemon import MonitoringDaemon
from ..models import ChannelIn, TargetIn
from ..state import StateTracker


def build_router(
    state: StateTracker,
    daemon: MonitoringDaemon,
    templates: Jinja2Templates,
    retention_days: int,
    username: str,
    password: str,
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

    router = APIRouter(dependencies=[Depends(verify_credentials)])

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        statuses = state.get_all_statuses(retention_days=retention_days)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "statuses": statuses,
                "retention_days": retention_days,
                "host_map": host_map,
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

    @router.get("/api/history/{target_name}")
    async def api_history(target_name: str, limit: int = 100, offset: int = 0):
        logs = state.get_history(target_name, limit=limit, offset=offset)
        return JSONResponse(
            content=[l.model_dump(mode="json") for l in logs]
        )

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
        state.add_channel(body.name, body.type, body.bot_token, body.chat_ids)
        return {"name": body.name}

    @router.delete("/api/channels/{name}", status_code=204)
    async def delete_channel(name: str):
        if not state.delete_channel(name):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Channel not found or defined in config.yaml"
            )
        daemon.remove_channel(name)

    return router
