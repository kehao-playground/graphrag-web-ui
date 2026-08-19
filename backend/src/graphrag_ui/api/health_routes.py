from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from graphrag_ui.adapters.db import get_session_factory


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
        return {"db": db, "graphrag": app.state.graphrag_version}

    app.include_router(router)
