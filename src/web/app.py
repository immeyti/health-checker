from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..state import StateTracker
from .routes import build_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    state: StateTracker,
    retention_days: int = 30,
    screenshots_dir: str = "screenshots",
) -> FastAPI:
    username = os.environ.get("MONITOR_USERNAME")
    password = os.environ.get("MONITOR_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "MONITOR_USERNAME and MONITOR_PASSWORD must be set in the environment"
        )

    app = FastAPI(title="Dashboard Monitor", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = build_router(
        state=state,
        templates=templates,
        retention_days=retention_days,
        username=username,
        password=password,
    )
    app.include_router(router)

    # Serve screenshots and videos as static assets
    screenshots_path = Path(screenshots_dir)
    screenshots_path.mkdir(parents=True, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=str(screenshots_path)), name="screenshots")

    videos_path = screenshots_path.parent / "videos"
    videos_path.mkdir(parents=True, exist_ok=True)
    app.mount("/videos", StaticFiles(directory=str(videos_path)), name="videos")

    return app
