import asyncio
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.api.schemas import ReadyOut
from graphrag_ui.config import get_settings


def register_health_routes(app):
    # The router must be built inside the function: create_app() is called
    # many times in tests, and a module-level router would accumulate
    # duplicate routes
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health():
        return {"status": "ok"}

    # The Helm readinessProbe is an httpGet, so kubelet sees only the status
    # code: a 200 carrying {"db": "error"} keeps routing traffic to a pod
    # that cannot serve it. Every condition this endpoint exists to report
    # therefore answers 503, with the same JSON body for operators.
    @router.get("/ready", response_model=ReadyOut, responses={503: {"model": ReadyOut}})
    async def ready(response: Response):
        db: Literal["ok", "error"]
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            db = "ok"
        # asyncpg surfaces a refused/unroutable connection as a bare OSError
        # that SQLAlchemy does not wrap — Postgres being down is the one
        # case that must not escape as an unstructured 500.
        except (SQLAlchemyError, OSError):
            db = "error"
        # Same measurement point as the enqueue preflight (services/jobs.py):
        # the workspaces ROOT, created if missing so disk_usage has a target.
        settings = get_settings()
        ws_root = Path(settings.workspaces_dir).resolve()
        ws_root.mkdir(parents=True, exist_ok=True)
        disk_free_mb = (await asyncio.to_thread(shutil.disk_usage, ws_root)).free // (1024 * 1024)
        out = ReadyOut(
            db=db,
            graphrag=app.state.graphrag_version,
            disk_free_mb=disk_free_mb,
            disk_ok=disk_free_mb >= settings.disk_watermark_mb,
        )
        if not out.ready:
            response.status_code = 503
        return out

    app.include_router(router)
