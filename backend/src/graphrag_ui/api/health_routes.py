import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.config import get_settings


def register_health_routes(app):
    # router 必須建在函式內:create_app() 在測試裡會被呼叫很多次,
    # 模組級 router 會不斷累積重複路由
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health():
        return {"status": "ok"}

    @router.get("/ready")
    async def ready():
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            db = "ok"
        except SQLAlchemyError:
            db = "error"
        # Same measurement point as the enqueue preflight (services/jobs.py):
        # the workspaces ROOT, created if missing so disk_usage has a target.
        settings = get_settings()
        ws_root = Path(settings.workspaces_dir).resolve()
        ws_root.mkdir(parents=True, exist_ok=True)
        disk_free_mb = (await asyncio.to_thread(shutil.disk_usage, ws_root)).free // (1024 * 1024)
        return {
            "db": db,
            "graphrag": app.state.graphrag_version,
            "disk_free_mb": disk_free_mb,
            "disk_ok": disk_free_mb >= settings.disk_watermark_mb,
        }

    app.include_router(router)
