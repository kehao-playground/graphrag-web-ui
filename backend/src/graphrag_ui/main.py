import importlib.metadata
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI

from graphrag_ui.api.health_routes import register_health_routes


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
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="GraphRAG Web UI", lifespan=lifespan)
    register_health_routes(
        app,
        db_ok=lambda: True,
        graphrag_version=None,  # Task 2 改讀 app.state
    )
    return app
