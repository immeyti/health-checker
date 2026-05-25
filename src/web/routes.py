from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from ..state import StateTracker


def build_router(
    state: StateTracker,
    templates: Jinja2Templates,
    retention_days: int,
    username: str,
    password: str,
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

    return router
