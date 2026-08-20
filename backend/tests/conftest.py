import os

import email_validator
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer

from graphrag_ui.adapters.db import make_engine, make_session_factory, reset_engine
from graphrag_ui.adapters.models import Base
from graphrag_ui.api import auth_routes
from graphrag_ui.config import get_settings
from graphrag_ui.main import create_app

# pydantic EmailStr 底層的 email-validator 把 .local 視為 special-use domain
# (RFC 6762 mDNS)一律拒絕 — 已驗證至 2.3.0 的所有版本皆如此。測試的
# admin@test.local 需要此 domain,僅在測試環境放寬(validate 時透過
# `from . import` 重新讀取,module 屬性 patch 會生效);正式環境維持嚴格驗證。

email_validator.SPECIAL_USE_DOMAIN_NAMES = [
    d for d in email_validator.SPECIAL_USE_DOMAIN_NAMES if d != "local"]


@pytest.fixture(scope="session")
def db_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
def migrated_db(db_url):
    os.environ["DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    command.upgrade(cfg, "head")
    return db_url


@pytest.fixture
async def clean_db(migrated_db):
    """每個測試前清空所有表。

    用 `client` 的測試會反覆建立同樣的 email(alice@test.local…),
    沒有這層隔離,第二個測試就會撞 unique constraint。
    表清單由 Base.metadata 推導,後續 task 新增 model 不用回來改這裡。
    """
    engine = make_engine(migrated_db)
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield


@pytest.fixture
async def db_session(clean_db, migrated_db) -> AsyncSession:
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def app(clean_db, monkeypatch, tmp_path):
    """測試用 app 實例 — 讓測試能設定 app.dependency_overrides(例如換 FakeInitializer)。"""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-123")
    # ≥32 bytes 的測試 secret — 太短會觸發 PyJWT InsecureKeyLengthWarning
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcd")
    get_settings.cache_clear()
    await reset_engine()          # env 變了,共享 engine 必須重建
    auth_routes._LOGIN_FAILURES.clear()  # 模組級速率限制會跨測試殘留(同桶累計 → 429)
    return create_app()


@pytest.fixture
async def client(app):
    # httpx 的 ASGITransport **不會**觸發 lifespan。少了 LifespanManager,
    # bootstrap_admin() 不會執行、app.state.graphrag_version 不存在
    # → 之後每個 task 的登入測試都會 401。
    async with (
        LifespanManager(app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()
