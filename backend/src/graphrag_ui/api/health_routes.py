from fastapi import APIRouter


def register_health_routes(app, db_ok, graphrag_version):
    # router 必須建在函式內:create_app() 在測試裡會被呼叫很多次,
    # 模組級 router 會不斷累積重複路由
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health():
        return {"status": "ok"}

    @router.get("/ready")
    async def ready():
        return {"db": "ok" if db_ok() else "error", "graphrag": graphrag_version}

    app.include_router(router)
