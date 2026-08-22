import asyncio
import importlib.metadata
import logging
import shutil
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.api.auth_routes import register_auth_routes
from graphrag_ui.api.deps import MUST_CHANGE_ALLOWED_PATHS, resolve_access_user
from graphrag_ui.api.dry_run_routes import register_dry_run_routes
from graphrag_ui.api.env_routes import register_env_routes
from graphrag_ui.api.files_routes import register_files_routes
from graphrag_ui.api.health_routes import register_health_routes
from graphrag_ui.api.jobs_routes import register_jobs_routes
from graphrag_ui.api.projects_routes import register_projects_routes
from graphrag_ui.api.query_routes import register_query_routes
from graphrag_ui.api.settings_routes import register_settings_routes
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


async def _retention_loop(stop: asyncio.Event) -> None:
    """Daily retention sweep (spec §6.3): once at startup, then every 24h.
    A failing sweep logs and waits for the next cycle; the stop event ends
    the loop promptly on shutdown."""
    while not stop.is_set():
        try:
            from graphrag_ui.services.retention import sweep_all
            await sweep_all()
        except Exception:
            logging.getLogger(__name__).warning(
                "retention sweep failed", exc_info=True)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.graphrag_version = _graphrag_version()  # 啟動偵測一次後快取(spec §6.1)
    async with get_session_factory()() as s:
        await bootstrap_admin(s)
    from graphrag_ui.services.runner_loop import run_loop
    app.state.runner_stop = asyncio.Event()
    app.state.runner_task = asyncio.create_task(run_loop(app.state.runner_stop))
    # Same stop event as the runner: setting it wakes both loops at shutdown.
    app.state.retention_task = asyncio.create_task(
        _retention_loop(app.state.runner_stop))
    yield
    app.state.runner_stop.set()
    # Cancel cleanly even if a sweep is mid-flight; the next daily pass
    # reclaims whatever this one skipped.
    app.state.retention_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.retention_task
    # In-flight subprocesses are NOT drained here (they keep writing to the
    # job log/DB); the next boot's stale reconcile finalizes them (spec §10).
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(app.state.runner_task, timeout=5)


def _register_must_change_guard(app: FastAPI) -> None:
    """強制改密碼的全域防護(spec:後端也要擋,不能只靠前端 Modal)。

    get_current_user(deps.py)在每個受保護端點做同樣檢查,但它只在
    「路由存在且宣告該依賴」時執行;尚未掛 get_current_user 依賴的路徑
    會在此提前收到 403,而不是 404 洩漏路由。
    token 無效時不攔,交給端點的 get_current_user 回 401。
    """

    @app.middleware("http")
    async def must_change_password_guard(request: Request, call_next):
        path = request.url.path
        auth = request.headers.get("Authorization", "")
        if (
            path.startswith("/api")
            and path not in MUST_CHANGE_ALLOWED_PATHS
            and auth.startswith("Bearer ")
        ):
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
    register_projects_routes(app)
    register_files_routes(app)
    register_env_routes(app)
    register_settings_routes(app)
    register_jobs_routes(app)
    register_dry_run_routes(app)
    register_query_routes(app)
    _register_must_change_guard(app)
    return app
