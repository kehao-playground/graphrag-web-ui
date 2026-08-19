import importlib.metadata
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.api.auth_routes import register_auth_routes
from graphrag_ui.api.deps import MUST_CHANGE_ALLOWED_PATHS, resolve_access_user
from graphrag_ui.api.health_routes import register_health_routes
from graphrag_ui.api.users_routes import register_users_routes
from graphrag_ui.services.auth import bootstrap_admin


def _graphrag_version() -> str:
    # 啟動偵測一次後快取(spec §6.1)。
    # graphrag 3.x CLI 沒有 --version 選項(typer 未宣告,exit 2),版本讀套件 metadata;
    # adapters 以 subprocess 呼叫 `graphrag`,故仍以 PATH 檢查 CLI 可用性 —
    # 容器內由 Dockerfile 的 ENV PATH 保證。

    if shutil.which("graphrag") is None:
        return "not-installed"
    try:
        return importlib.metadata.version("graphrag")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.graphrag_version = _graphrag_version()  # 啟動偵測一次後快取(spec §6.1)
    async with get_session_factory()() as s:
        await bootstrap_admin(s)
    yield


# must_change_password 的檢查路徑集合統一定義在 deps.MUST_CHANGE_ALLOWED_PATHS


def _register_must_change_guard(app: FastAPI) -> None:
    """強制改密碼的全域防護(spec:後端也要擋,不能只靠前端 Modal)。

    get_current_user(deps.py)在每個受保護端點做同樣檢查,但它只在
    「路由存在且宣告該依賴」時執行;尚未實作/未掛依賴的路徑(例如
    後續 task 的 /api/admin/*)會在此提前收到 403,而不是 404 洩漏路由。
    token 無效時不攔,交給端點的 get_current_user 回 401。
    """

    @app.middleware("http")
    async def must_change_password_guard(request: Request, call_next):
        path = request.url.path
        auth = request.headers.get("Authorization", "")
        if (path.startswith("/api") and path not in MUST_CHANGE_ALLOWED_PATHS
                and auth.startswith("Bearer ")):
            async with get_session_factory()() as session:
                user = await resolve_access_user(auth[7:], session)
            if user is not None and user.must_change_password:
                return JSONResponse({"detail": "password change required"}, status_code=403)
        return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="GraphRAG Web UI", lifespan=lifespan)
    register_health_routes(app)
    register_auth_routes(app)
    register_users_routes(app)
    return app
