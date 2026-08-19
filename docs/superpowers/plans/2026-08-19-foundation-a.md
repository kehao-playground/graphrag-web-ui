# Foundation-A 實作計畫(GraphRAG Web UI Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立前後端 scaffold 與部署骨架,並交付帳號系統(JWT + 輪替 refresh)、管理員使用者管理、專案 CRUD + 成員 + 權限矩陣(專案建立時執行 `graphrag init`)。

**Architecture:** FastAPI 單體(clean architecture 精神:domain 純邏輯 / services 使用案例 / adapters 基礎設施 / api 路由),PostgreSQL 存應用資料,multi-stage Docker;React SPA 由 nginx serve 並反代 `/api`。Spec:`docs/superpowers/specs/2026-08-19-graphrag-web-ui-design.md`。

**Tech Stack:** Python 3.12 + uv、FastAPI、SQLAlchemy 2 (async) + asyncpg、alembic、PyJWT、argon2-cffi、graphrag(pinned);React 18 + Vite + TypeScript + Ant Design 5、TanStack Query v5、React Router v6、Zustand;PostgreSQL 16。

## Global Constraints

- 所有環境設定走環境變數,名稱固定:`DATABASE_URL`、`WORKSPACES_DIR`、`JWT_SECRET`、`BOOTSTRAP_ADMIN_EMAIL`、`BOOTSTRAP_ADMIN_PASSWORD`(spec §8.3)
- graphrag 整合只准出現在 `adapters/`,以 subprocess 呼叫 CLI;`domain/`、`services/` 禁止 import FastAPI / SQLAlchemy / graphrag(spec §9)
- DB schema 一律 alembic migration,禁止手動改表(spec §9)
- Token:access 15 分鐘;refresh 7 天、輪替式(每次 refresh 換發並作廢舊的)、DB 存 hash(spec §8.4)
- 密碼:argon2;`.env` 與秘密永不回明文(spec §10)
- 專案 = graphrag root 原封不動,workspace 路徑 = `<WORKSPACES_DIR>/<project_id>`(spec §3、§4)
- pytest asyncio_mode=auto;前端 vitest + RTL;每個 task 以綠燈測試 + conventional commit 收尾
- graphrag 套件版本於 Task 1 鎖定後,同步更新 spec §13 第 3 列的版本欄位

## 檔案結構總覽

```
backend/
  pyproject.toml  Dockerfile  alembic.ini
  migrations/            # alembic env + versions
  src/graphrag_ui/
    config.py            # pydantic-settings 環境變數
    main.py              # create_app() + lifespan(bootstrap admin、graphrag 版本快取)
    domain/permissions.py
    adapters/{db.py, models.py, workspace.py}
    services/{auth.py, users.py, projects.py, audit.py}
    api/{deps.py, schemas.py, auth_routes.py, users_routes.py,
         projects_routes.py, health_routes.py}
  tests/{conftest.py, test_health.py, test_auth.py, test_users.py,
         test_permissions.py, test_projects.py}
frontend/
  package.json  vite.config.ts  tsconfig.json  index.html  Dockerfile  nginx.conf
  src/{main.tsx, App.tsx, api/{client.ts, types.ts}, stores/auth.ts,
       pages/{Login.tsx, Projects.tsx, ProjectDetail.tsx, AdminUsers.tsx},
       components/{Layout.tsx, ProtectedRoute.tsx}}
  src/pages/__tests__/{Login.test.tsx, Projects.test.tsx}
deploy/helm/graphrag-ui/  # Chart.yaml values.yaml templates/...
docker-compose.yml  .env.example  .gitignore
```

---

### Task 1: 後端 scaffold + health/ready + graphrag 版本鎖定

**Files:**
- Create: `backend/pyproject.toml`、`backend/src/graphrag_ui/{__init__.py,config.py,main.py}`、`backend/src/graphrag_ui/api/{__init__.py,health_routes.py}`、`backend/tests/{__init__.py,test_health.py}`、`backend/Dockerfile`、`.gitignore`、`.env.example`

**Interfaces:**
- Produces: `create_app() -> FastAPI`;`GET /api/health` → `{"status":"ok"}`;`GET /api/ready` → `{"db":"ok","graphrag":"<version>"}`;`Settings` 欄位如上 Global Constraints;後續 task 的路由都掛在 `create_app()` 內。

- [ ] **Step 1: 專案初始化與依賴**

```bash
mkdir -p backend && cd backend
uv init --package . --name graphrag-ui --python 3.12
# graphrag 先加:它的依賴樹最大,先讓它鎖住 pydantic/httpx 等共用套件
uv add graphrag   # 解析當下最新 stable
uv add fastapi 'uvicorn[standard]' 'sqlalchemy[asyncio]' asyncpg alembic \
       pydantic-settings argon2-cffi pyjwt pyyaml
```

若解析衝突(fastapi/pydantic 版本對不上),以 graphrag 的 pin 為準降版其他套件,**不要動 graphrag** — 查詢服務(Phase 4)要 in-process import 它。

然後把 `pyproject.toml` 中 `graphrag` 固定為 `==<resolved>`(執行 `uv pip show graphrag` 或看 uv.lock 取得版本),並在 spec `docs/superpowers/specs/2026-08-19-graphrag-web-ui-design.md` §13 第 3 列補上鎖定版本。

dev 群組:

```bash
uv add --dev pytest pytest-asyncio httpx asgi-lifespan 'testcontainers[postgres]' ruff
```

`pyproject.toml` 加:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 寫失敗測試** `backend/tests/test_health.py`

```python
from graphrag_ui.main import create_app
from httpx import ASGITransport, AsyncClient


async def test_health():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: FAIL(`ModuleNotFoundError: graphrag_ui.main` 或 import 鏈錯誤)

- [ ] **Step 4: 實作**

`backend/src/graphrag_ui/config.py`:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://graphrag:graphrag@localhost:5432/graphrag"
    workspaces_dir: str = "./data/workspaces"
    jwt_secret: str = "dev-secret-change-me"
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/src/graphrag_ui/api/health_routes.py`:

```python
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
```

(此檔 Task 2 會改為讀 DB;先用注入閉包,Task 2 重構為真檢查。)

`backend/src/graphrag_ui/main.py`:

```python
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI

from graphrag_ui.api.health_routes import register_health_routes


def _graphrag_version() -> str:
    # 依賴 graphrag 在 PATH 上;容器內由 Dockerfile 的 ENV PATH 保證

    try:
        out = subprocess.run(
            ["graphrag", "--version"], capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() or "unknown"
    except Exception:
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
```

注意:health_routes 的 `graphrag_version=None` 會讓 ready 測不出真值 — 本 task 的 ready 測試只斷言 key 存在;Task 2 重構。`.gitignore`:`.venv/`、`__pycache__/`、`data/`、`node_modules/`、`dist/`、`.env`。`.env.example` 列出 Global Constraints 五個變數與預設註解。

- [ ] **Step 5: 跑測試確認過**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Dockerfile(後續 task 共用)**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
WORKDIR /app
# graphrag CLI 必須在 PATH 上 — adapters 以 subprocess 呼叫 `graphrag`,
# 直接跑 .venv/bin/uvicorn 不會注入 venv 的 PATH,會造成「本機過、容器炸」
ENV PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
# src/ 還沒 COPY,不能安裝專案本身,否則 build 失敗
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
EXPOSE 8000
# main.py 只有 create_app(),沒有模組級 app → 必須 --factory
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory graphrag_ui.main:create_app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 7: Commit**

```bash
git add backend .gitignore .env.example
git commit -m "feat(backend): scaffold FastAPI app with health/ready and pinned graphrag"
```

---

### Task 2: Postgres、SQLAlchemy models、alembic(users / refresh_tokens)

**Files:**
- Create: `backend/src/graphrag_ui/adapters/{__init__.py,db.py,models.py}`、`backend/migrations/*`(alembic init 產物 + 一支 version)、`backend/alembic.ini`、`backend/tests/conftest.py`

**Interfaces:**
- Consumes: Task 1 `Settings.database_url`
- Produces: `engine`/`session_factory`(`adapters/db.py`);models `User(id: UUID, email: str unique, password_hash: str, display_name: str, role: str["admin"|"user"], is_active: bool, must_change_password: bool, created_at: datetime)`、`RefreshToken(id, user_id FK, token_hash unique, expires_at, created_at)`;`migrations/versions/<rev>_foundation_a_users.py`;conftest fixtures:`db_url`(testcontainers)、`client`(已跑 migration 的 AsyncClient)、`db_session`。

- [ ] **Step 0: 起一個開發用 Postgres**

`docker-compose.yml` 要到 Task 10 才建立,但 `alembic revision --autogenerate` / `upgrade head` 需要連得上 DB:

```bash
docker run -d --name grui-pg -p 5432:5432 \
  -e POSTGRES_USER=graphrag -e POSTGRES_PASSWORD=graphrag -e POSTGRES_DB=graphrag \
  postgres:16-alpine
```

(測試自己會起 testcontainer,這個只給 alembic 開發流程用。)

- [ ] **Step 1: alembic 與 models 先行**

```bash
cd backend && uv run alembic init migrations
```

`alembic.ini` 設 `sqlalchemy.url`(留空,由 env.py 覆寫)。`migrations/env.py` 改動兩處:

```python
from graphrag_ui.config import get_settings
from graphrag_ui.adapters.models import Base
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

`adapters/db.py`:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from graphrag_ui.config import get_settings

_engine = None
_factory = None


def make_engine(url: str | None = None):
    """顯式 URL 用(測試 fixture、一次性腳本)。"""
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker:
    """應用用的共享 factory — **必須 lazy**。

    模組級直接建 engine 會在 import 時就讀 get_settings(),
    早於測試 fixture 設定 DATABASE_URL,導致整個測試連到錯的 DB。
    """
    global _engine, _factory
    if _factory is None:
        _engine = make_engine()
        _factory = make_session_factory(_engine)
    return _factory


async def reset_engine() -> None:
    """測試用:環境變數變更後丟棄快取的 engine(記得 dispose,否則連線池會累積)。"""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
```

`adapters/models.py`(本 task 只建兩張表;jobs 等在後續階段各自的 migration):

```python
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin|user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`revoked_at` 不是可有可無:輪替式 refresh 必須保留已消費的 token 才能做**重用偵測**(見 Task 3)。已撤銷的列由後續階段的清理任務定期刪除。

- [ ] **Step 2: 產生 migration 並驗證**

```bash
uv run alembic revision --autogenerate -m "foundation_a users and refresh tokens"
uv run alembic upgrade head
```

- [ ] **Step 3: conftest(testcontainers Postgres)**

`backend/tests/conftest.py`:

```python
import os

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
from graphrag_ui.config import get_settings
from graphrag_ui.main import create_app


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
```

`client` fixture:

```python
@pytest.fixture
async def client(clean_db, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-123")
    get_settings.cache_clear()
    await reset_engine()          # env 變了,共享 engine 必須重建
    app = create_app()
    # httpx 的 ASGITransport **不會**觸發 lifespan。少了 LifespanManager,
    # bootstrap_admin() 不會執行、app.state.graphrag_version 不存在
    # → 之後每個 task 的登入測試都會 401。
    async with LifespanManager(app) as managed:
        async with AsyncClient(transport=ASGITransport(app=managed.app),
                               base_url="http://t") as c:
            yield c
    await reset_engine()
```

`clean_db` 是 function scope,`client` 與 `db_session` 同時使用時 pytest 只會建立一次 → 同一個測試內兩者看到同一份乾淨資料。

- [ ] **Step 4: 寫測試**(放到 `tests/test_health.py` 追加)

```python
async def test_ready_with_db(client):
    r = await client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok"
    assert body["graphrag"]  # 啟動時快取的版本字串
```

同時把 `main.py` 的 `register_health_routes` 重構為接收 `app`,ready 內執行 `SELECT 1`(透過 `get_session_factory()`,不要自己 `make_engine()`),`graphrag_version` 讀 `app.state.graphrag_version`。

- [ ] **Step 5: 跑全部測試**

Run: `cd backend && uv run pytest -v`
Expected: PASS(health 兩條;testcontainers 首次拉 image 會久)

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(backend): postgres models, alembic migration, testcontainers fixtures"
```

---

### Task 3: Auth(argon2 + JWT + refresh 輪替 + bootstrap admin)

**Files:**
- Create: `backend/src/graphrag_ui/services/{__init__.py,auth.py}`、`backend/src/graphrag_ui/api/{deps.py,schemas.py,auth_routes.py}`、`backend/tests/test_auth.py`
- Modify: `backend/src/graphrag_ui/main.py`(lifespan bootstrap admin、掛路由)

**Interfaces:**
- Consumes: Task 2 `db_session_factory`、`User`、`RefreshToken`、`Settings`
- Produces:
  - `services/auth.py`:`hash_password(pw: str) -> str`、`verify_password(pw, hashed) -> bool`、`create_access_token(user: User) -> str`、`issue_refresh_token(session, user_id: UUID) -> str`(opaque,回明文只在發行當下)、`rotate_refresh(session, token: str) -> tuple[UUID, str] | None`(回 `(user_id, new_refresh)`;舊 token 標記 `revoked_at`。**已消費的 token 再次出現 → 撤銷該使用者全部 token**)、`revoke_refresh(session, token)`、`revoke_all_for_user(session, user_id)`、`authenticate(session, email, password) -> User | None`、`bootstrap_admin(session)`
  - API:`POST /api/auth/login` `{email,password}` → `200 {access_token, refresh_token, user:{id,email,display_name,role,must_change_password}}`(錯誤 401);`POST /api/auth/refresh` `{refresh_token}` → `200 {access_token, refresh_token}`(無效 401);`POST /api/auth/logout` `{refresh_token}` → `204`;`POST /api/auth/change-password` `{current_password,new_password}`(需 Bearer)→ `204`,設 `must_change_password=False` 並撤銷該使用者全部 refresh;`GET /api/auth/me`(需 Bearer)→ `UserOut`(**前端重整後恢復 session 必需**:`/auth/refresh` 只回 token,拿不到 user)
  - login 端點加 per-IP 速率限制(簡單的記憶體滑動視窗即可,預設 10 次/分鐘)
  - `api/deps.py`:`get_current_user`(Bearer JWT → User;401 失敗)— 後續 task 共用

- [ ] **Step 1: 寫失敗測試** `backend/tests/test_auth.py`

```python
async def test_bootstrap_admin_login_and_forced_change(client):
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["must_change_password"] is True
    # 強制改密碼流程
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    r2 = await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": "admin-pass-123", "new_password": "new-pass-456"})
    assert r2.status_code == 204
    r3 = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "new-pass-456"})
    assert r3.json()["user"]["must_change_password"] is False


async def test_login_wrong_password(client):
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "nope"})
    assert r.status_code == 401


async def test_refresh_rotation_invalidates_old(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    old = body["refresh_token"]
    r1 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r1.status_code == 200
    r2 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r2.status_code == 401  # 舊的已被作廢


async def test_refresh_reuse_revokes_family(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    old = body["refresh_token"]
    new = (await client.post("/api/auth/refresh", json={"refresh_token": old})).json()["refresh_token"]
    # 拿已消費的舊 token 再試一次 → 視為外洩,連新發的也一併作廢
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": new})).status_code == 401


async def test_me_returns_current_user(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    # must_change_password 為真時,/me 與 change-password 以外的端點應被擋
    assert (await client.get("/api/auth/me", headers=hdr)).json()["email"] == "admin@test.local"
    assert (await client.get("/api/admin/users", headers=hdr)).status_code == 403


async def test_logout_revokes(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    await client.post("/api/auth/logout", json={"refresh_token": body["refresh_token"]})
    r = await client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: FAIL(404,路由不存在)

- [ ] **Step 3: 實作 service**

`services/auth.py`:

```python
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import RefreshToken, User
from graphrag_ui.config import get_settings

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user.id), "role": user.role, "type": "access",
         "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes)},
        s.jwt_secret, algorithm="HS256")


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> str:
    s = get_settings()
    token = secrets.token_urlsafe(48)
    session.add(RefreshToken(
        user_id=user_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days)))
    await session.commit()
    return token


async def _find(session: AsyncSession, token: str) -> RefreshToken | None:
    h = hashlib.sha256(token.encode()).hexdigest()
    return (await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == h))).scalar_one_or_none()


async def rotate_refresh(session: AsyncSession, token: str) -> tuple[uuid.UUID, str] | None:
    """回 (user_id, new_refresh);失敗回 None。呼叫端自行以 user_id 發 access token。"""
    row = await _find(session, token)
    if row is None:
        return None
    if row.revoked_at is not None:
        # 已消費過的 token 再次出現 = 疑似外洩 → 撤銷該使用者整個 token 家族
        await revoke_all_for_user(session, row.user_id)
        return None
    if row.expires_at < datetime.now(UTC):
        return None
    row.revoked_at = datetime.now(UTC)   # 標記而非刪除,重用偵測才成立
    await session.commit()
    return row.user_id, await issue_refresh_token(session, row.user_id)


async def revoke_refresh(session: AsyncSession, token: str) -> None:
    row = await _find(session, token)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    await session.commit()


_DUMMY_HASH = _ph.hash("dummy-for-constant-time")


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)   # 拉平回應時間,避免以時間差枚舉帳號
        return None
    return user if verify_password(password, user.password_hash) else None


async def bootstrap_admin(session: AsyncSession) -> None:
    s = get_settings()
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        return
    admin = (await session.execute(select(User).where(User.role == "admin"))).scalar_one_or_none()
    if admin is not None:
        return
    session.add(User(email=s.bootstrap_admin_email, password_hash=hash_password(
        s.bootstrap_admin_password), display_name="Administrator",
        role="admin", must_change_password=True))
    await session.commit()
```

- [ ] **Step 4: 實作路由與 deps**

`api/schemas.py`(本 task 部分):

```python
from pydantic import BaseModel, EmailStr, Field


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
```

`api/deps.py`:

```python
import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import User
from graphrag_ui.config import get_settings

_bearer = HTTPBearer(auto_error=False)


async def get_db():
    # 每個請求開一個 session;factory 本身是 lazy singleton(adapters/db.py)
    async with get_session_factory()() as session:
        yield session
```

實作細節:**不要在模組層建立 engine**(import 時機早於測試設定 `DATABASE_URL`,會綁到錯的資料庫)——一律走 `get_session_factory()`。`get_current_user(creds: Annotated[...Depends(_bearer)], db) -> User`:無 creds → 401;`jwt.decode(token, secret, algorithms=["HS256"])` 後檢查 `payload["type"] == "access"`(**token 沒有 `aud` claim,所以不要傳 `audience=`**,否則必定 InvalidAudienceError);過期 → 401;`db.get(User, uuid.UUID(payload["sub"]))`;`user.is_active` 為假 → 401;`user.must_change_password` 為真且請求路徑不是 `/api/auth/change-password` 或 `/api/auth/me` → 403 `{"detail":"password change required"}`(後端也要擋,不能只靠前端 Modal)。

`api/auth_routes.py`:五個端點(login/refresh/logout/change-password/me)照 Interfaces 規格,login 成功回傳 `UserOut.model_validate(user, from_attributes=True)` 序列化(`model_config = ConfigDict(from_attributes=True)` 加在 UserOut)。

速率限制實作(Interfaces 承諾):`auth_routes` 模組級 `_login_attempts: dict[str, deque[datetime]]`,login 進點先滑動視窗清理並檢查(>10 次/分 → 429 `{"detail":"too many attempts"}`),失敗也計數。測試不覆蓋 — 純計數邏輯,計時斷言易 flaky。

`main.py` lifespan 內、yield 前:

```python
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.services.auth import bootstrap_admin
...
async with get_session_factory()() as s:
    await bootstrap_admin(s)
```

並 include auth router。**注意 lifespan 只有在 ASGI server(或測試的 `LifespanManager`)下才會跑** — 見 Task 2 的 `client` fixture。

- [ ] **Step 5: 跑測試**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(backend): JWT auth with rotating refresh tokens and bootstrap admin"
```

---

### Task 4: 管理員使用者管理 + audit log

**Files:**
- Create: `backend/src/graphrag_ui/services/{users.py,audit.py}`、`backend/src/graphrag_ui/api/users_routes.py`、`backend/tests/test_users.py`
- Modify: `backend/src/graphrag_ui/adapters/models.py`(+`AuditLog`)、新 migration、`main.py` 掛路由

**Interfaces:**
- Consumes: Task 3 `get_current_user`、`hash_password`、`revoke_all_for_user`
- Produces:
  - `services/audit.py`:`async def audit(session, actor_id: UUID | None, action: str, target_type: str, target_id: str, payload: dict | None = None)`
  - `services/users.py`:`create_user(session, email, display_name, password, actor_id)`、`update_user(session, user, display_name=None, role=None, is_active=None, actor_id)`(停用時 `revoke_all_for_user`)、`reset_password(session, user, new_password, actor_id)`(設 `must_change_password=True` 並撤銷全部 refresh)
  - API(全部要求 `role=="admin"`,否則 403):`GET /api/admin/users` → `[UserOut]`;`POST /api/admin/users` `{email,display_name,password}` → 201 `UserOut`;`PATCH /api/admin/users/{id}` `{display_name?,role?,is_active?}` → `UserOut`;`POST /api/admin/users/{id}/reset-password` `{new_password}` → 204。audit action 值:`user.created`、`user.updated`、`user.password_reset`

- [ ] **Step 1: 寫失敗測試** `backend/tests/test_users.py`

```python
ADMIN_PW = "admin-new-pass-1"


async def _admin_token(client):
    """bootstrap admin 的 must_change_password=True — 換完密碼才拿得到可用 token。"""
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": "admin-pass-123", "new_password": ADMIN_PW})
    r2 = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": ADMIN_PW})
    return {"Authorization": f"Bearer {r2.json()['access_token']}"}


async def test_admin_crud_and_audit(client, db_session):
    hdr = await _admin_token(client)
    r = await client.post("/api/admin/users", headers=hdr, json={
        "email": "u1@test.local", "display_name": "User One", "password": "pass-12345"})
    assert r.status_code == 201
    uid = r.json()["id"]
    r2 = await client.patch(f"/api/admin/users/{uid}", headers=hdr,
                            json={"display_name": "User 1b"})
    assert r2.json()["display_name"] == "User 1b"
    r3 = await client.post(f"/api/admin/users/{uid}/reset-password",
                           headers=hdr, json={"new_password": "reset-12345"})
    assert r3.status_code == 204
    # audit 直接驗表 — 不要用「停用後登不進去」來推論 audit 有寫
    from sqlalchemy import select

    from graphrag_ui.adapters.models import AuditLog
    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"user.created", "user.updated", "user.password_reset"} <= actions
    r4 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"is_active": False})
    assert r4.json()["is_active"] is False
    r5 = await client.post("/api/auth/login", json={
        "email": "u1@test.local", "password": "reset-12345"})
    assert r5.status_code == 401


async def test_non_admin_forbidden(client):
    hdr = await _admin_token(client)
    await client.post("/api/admin/users", headers=hdr, json={
        "email": "u2@test.local", "display_name": "U2", "password": "pass-12345"})
    r = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "pass-12345"})
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # 新建帳號 must_change_password=True → 先換密碼才能用其他端點
    await client.post("/api/auth/change-password", headers=tok, json={
        "current_password": "pass-12345", "new_password": "u2-pass-6789"})
    r2 = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "u2-pass-6789"})
    user_tok = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r3 = await client.get("/api/admin/users", headers=user_tok)
    assert r3.status_code == 403
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/test_users.py -v`
Expected: FAIL(404)

- [ ] **Step 3: AuditLog model + migration**

`adapters/models.py` 追加:

```python
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

(`from sqlalchemy.dialects.postgresql import JSONB`)

```bash
uv run alembic revision --autogenerate -m "foundation_a audit log"
uv run alembic upgrade head
```

`services/audit.py`:

```python
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from graphrag_ui.adapters.models import AuditLog


async def audit(session: AsyncSession, actor_id: uuid.UUID | None, action: str,
                target_type: str, target_id: str, payload: dict | None = None) -> None:
    # 只 add,**不 commit**:交易邊界屬於呼叫端。
    # 若這裡 commit,會把呼叫端尚未完成的變更一起送出
    #(例如 create_project 還沒跑完 graphrag init 就被 commit)。
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type,
                         target_id=target_id, payload=payload))
```

- [ ] **Step 4: services/users.py + 路由**

`services/users.py` 照 Interfaces 實作:

- 密碼用 `hash_password`;`create_user` 一律設 `must_change_password=True`(與 `reset_password` 語意一致 — 管理員設的初始密碼不該長期使用)
- `update_user` 停用時與 `reset_password` 皆呼叫 `revoke_all_for_user`
- 每個寫入操作呼叫 `audit(...)`,並由 **service 函式自己 `await session.commit()`**(audit 已改為不 commit)

路由:`APIRouter(prefix="/api/admin/users", dependencies=[Depends(require_admin)])`;`require_admin = get_current_user` 再檢查 `user.role == "admin"` 否則 403。

- `GET` 明確 `ORDER BY created_at`(測試與前端都不該依賴隱含順序)
- PATCH 不允許改自己的 `is_active` 與 `role`(回 400)
- PATCH 也不允許把**最後一個 active admin** 降級或停用(回 400,否則系統會被鎖死)

因為 `create_user` 會設 `must_change_password=True`,`test_non_admin_forbidden` 建立 u2 之後要先呼叫一次 change-password 才能取得可用的一般使用者 token。

- [ ] **Step 5: 跑測試**

Run: `cd backend && uv run pytest tests/test_users.py tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(backend): admin user management with audit log"
```

---

### Task 5: Domain 權限矩陣(純邏輯)

**Files:**
- Create: `backend/src/graphrag_ui/domain/{__init__.py,permissions.py}`、`backend/tests/test_permissions.py`

**Interfaces:**
- Consumes: 無(純函式,零 import 專案外依賴)
- Produces:

```python
class Action(StrEnum):
    manage_users = "manage_users"
    create_project = "create_project"
    view_project = "view_project"
    update_project = "update_project"
    delete_project = "delete_project"
    manage_members = "manage_members"


def can(user_role: str, is_active: bool, action: Action,
        project_role: str | None = None) -> bool
```

`project_role ∈ {"owner","editor","viewer",None}`;矩陣 = spec §5。

- [ ] **Step 1: 寫測試(先完整枚舉矩陣)**

```python
import pytest
from graphrag_ui.domain.permissions import Action, can


# 注意:每個 case 的 expected 是**一般使用者**的預期值;
# admin 全通過與停用帳號全拒絕由測試本體最後兩行統一覆蓋,不要再展開成 case。
@pytest.mark.parametrize("action,project_role,expected", [
    # 一般使用者 + owner
    (Action.view_project, "owner", True),
    (Action.update_project, "owner", True),
    (Action.delete_project, "owner", True),
    (Action.manage_members, "owner", True),
    (Action.manage_users, "owner", False),
    # editor
    (Action.view_project, "editor", True),
    (Action.update_project, "editor", True),
    (Action.delete_project, "editor", False),
    (Action.manage_members, "editor", False),
    # viewer
    (Action.view_project, "viewer", True),
    (Action.update_project, "viewer", False),
    (Action.manage_members, "viewer", False),
    # 非成員
    (Action.view_project, None, False),
    # 建專案:任何 active 使用者
    (Action.create_project, None, True),
])
def test_matrix(action, project_role, expected):
    assert can("user", True, action, project_role) is expected
    # admin 全部允許
    assert can("admin", True, action, project_role) is True
    # 停用帳號全部拒絕
    assert can("admin", False, action, project_role) is False
```

- [ ] **Step 2: 跑測試確認失敗**(`uv run pytest tests/test_permissions.py -v` → ModuleNotFoundError)

- [ ] **Step 3: 實作**

```python
from enum import StrEnum


class Action(StrEnum):
    manage_users = "manage_users"
    create_project = "create_project"
    view_project = "view_project"
    update_project = "update_project"
    delete_project = "delete_project"
    manage_members = "manage_members"


_PROJECT_ACTIONS: dict[Action, set[str]] = {
    Action.view_project: {"owner", "editor", "viewer"},
    Action.update_project: {"owner", "editor"},
    Action.delete_project: {"owner"},
    Action.manage_members: {"owner"},
}


def can(user_role: str, is_active: bool, action: Action,
        project_role: str | None = None) -> bool:
    if not is_active:
        return False
    if user_role == "admin":
        return True
    if action is Action.manage_users:
        return False
    if action is Action.create_project:
        return True
    allowed = _PROJECT_ACTIONS.get(action)
    return allowed is not None and project_role in allowed
```

- [ ] **Step 4: 跑測試確認過** → **Step 5: Commit** `feat(backend): pure permission matrix`

---

### Task 6: 專案 CRUD + 成員 + graphrag init workspace

**Files:**
- Create: `backend/src/graphrag_ui/adapters/workspace.py`、`backend/src/graphrag_ui/services/projects.py`、`backend/src/graphrag_ui/api/projects_routes.py`、`backend/tests/test_projects.py`
- Modify: `adapters/models.py`(+`Project`、`ProjectMember`)、新 migration、`main.py`

**Interfaces:**
- Consumes: Task 3 `get_current_user`、Task 4 `audit`、Task 5 `Action/can`
- Produces:
  - `adapters/workspace.py`:

```python
class WorkspaceInitializer(Protocol):
    def init(self, root: Path, input_file_type: str) -> None: ...

class GraphragInitInitializer:  # subprocess graphrag init + patch settings.yaml
    def init(self, root: Path, input_file_type: str) -> None: ...
```

  - models:`Project(id UUID pk, name, slug unique, description, owner_id FK users, input_file_type ∈ text|csv|json, created_at)`、`ProjectMember(project_id, user_id, role ∈ owner|editor|viewer, PK(project_id,user_id))`。**兩個 FK 都要明寫 `ondelete="CASCADE"`**(`ProjectMember.project_id` → projects、`ProjectMember.user_id` → users),刪專案時的成員清理靠它,不要在 service 手動刪
  - `services/projects.py`:`create_project(session, name, description, input_file_type, creator) -> Project`、`get_project_role(session, project_id, user_id) -> str | None`、`list_projects(session, user)`(admin 看得到全部專案,一般使用者只看得到自己是成員的)、`update_project` / `delete_project`(連同 workspace 目錄,先驗證路徑在 `WORKSPACES_DIR` 內)、`set_member(session, project, user_id, role)`、`remove_member`
  - API:`GET/POST /api/projects`、`GET/PATCH/DELETE /api/projects/{id}`、`GET /api/projects/{id}/members`、`PUT/DELETE /api/projects/{id}/members/{user_id}`;權限用 `can()`;403 訊息固定 `{"detail":"forbidden"}`

- [ ] **Step 1: models + migration**(同 Task 4 模式;`slug = slugify(name)`,碰撞時加短隨機後綴)

- [ ] **Step 2: 寫失敗測試** `backend/tests/test_projects.py`

```python
import yaml


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """所有新帳號(含 bootstrap admin)must_change_password=True — 換完密碼才可用。"""
    hdr = await _login(client, email, initial_pw)
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": initial_pw, "new_password": new_pw})
    return await _login(client, email, new_pw)


async def _setup_two_users(client):
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    await client.post("/api/admin/users", headers=admin, json={
        "email": "alice@test.local", "display_name": "Alice", "password": "alice-pass-1"})
    await client.post("/api/admin/users", headers=admin, json={
        "email": "bob@test.local", "display_name": "Bob", "password": "bob-pass-1234"})
    return admin


async def test_create_project_runs_init_and_adds_owner(client, tmp_path):
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post("/api/projects", headers=alice, json={
        "name": "Research Corpus", "input_file_type": "text"})
    assert r.status_code == 201
    pid = r.json()["id"]
    ws = tmp_path / "ws" / pid
    assert (ws / "settings.yaml").exists()      # graphrag init 真的跑過
    assert (ws / "input").exists()
    cfg = yaml.safe_load((ws / "settings.yaml").read_text())
    assert cfg["input"]["type"] == "text"  # 不可寫 `"text" in yaml_text`:
    #   settings.yaml 本來就有 text-embedding-3-large 等字串,那樣寫就算沒 patch 也會過
    members = (await client.get(f"/api/projects/{pid}/members", headers=alice)).json()
    assert members[0]["email"] == "alice@test.local" and members[0]["role"] == "owner"


async def test_permission_matrix_enforced(client):
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "P1", "input_file_type": "text"})).json()["id"]
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 403
    # alice 加 bob 為 viewer → 可讀不可改
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 200
    assert (await client.patch(f"/api/projects/{pid}", headers=bob,
                               json={"name": "X"})).status_code == 403
    # bob 不能管理成員
    assert (await client.delete(f"/api/projects/{pid}/members/{bob_id}",
                                headers=bob)).status_code == 403
    # 非 owner 不能刪專案;owner 可以
    assert (await client.delete(f"/api/projects/{pid}", headers=bob)).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}", headers=alice)).status_code == 204


async def test_delete_project_removes_workspace(client, tmp_path):
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "P2", "input_file_type": "csv"})).json()["id"]
    assert (tmp_path / "ws" / pid).exists()
    await client.delete(f"/api/projects/{pid}", headers=alice)
    assert not (tmp_path / "ws" / pid).exists()
```

- [ ] **Step 3: 跑測試確認失敗**(`uv run pytest tests/test_projects.py -v` → 404)

- [ ] **Step 4: 實作 workspace adapter**

```python
import asyncio
import subprocess
from pathlib import Path
from typing import Protocol

import yaml

# 鍵名已對 graphrag 原始碼驗證:格式是 input.type(InputConfig.type,無 file_type 欄位;
# 儲存後端是另一個頂層區段 input_storage.type)。file_pattern 是 regex(TextFileReader
# 預設 r".*\.txt$"),不是 glob。text 對 txt+md(spec §2 白名單)。
_FILE_PATTERNS = {"text": r".*\.(txt|md)$", "csv": r".*\.csv$", "json": r".*\.json$"}
_ALLOWED = set(_FILE_PATTERNS)


class WorkspaceInitError(RuntimeError):
    """graphrag init 失敗。由 api 層轉成 HTTP — services 不得 import FastAPI。"""


class WorkspaceInitializer(Protocol):
    async def init(self, root: Path, input_file_type: str) -> None: ...


class GraphragInitInitializer:
    async def init(self, root: Path, input_file_type: str) -> None:
        if input_file_type not in _ALLOWED:
            msg = f"unsupported input_file_type: {input_file_type}"
            raise ValueError(msg)
        # subprocess.run 是阻塞的,直接寫在 async route 會卡住整個 event loop
        #(單副本部署 = 全服務凍結數秒)
        await asyncio.to_thread(self._run, root, input_file_type)

    def _run(self, root: Path, input_file_type: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["graphrag", "init", "--root", str(root)],
                           check=True, capture_output=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError) as e:   # FileNotFoundError = CLI 不在 PATH
            raise WorkspaceInitError(str(e)) from e
        settings_path = root / "settings.yaml"
        data = yaml.safe_load(settings_path.read_text())
        input_cfg = data.setdefault("input", {})
        input_cfg["type"] = input_file_type
        input_cfg["file_pattern"] = _FILE_PATTERNS[input_file_type]
        settings_path.write_text(yaml.safe_dump(data, sort_keys=False))
        # InputConfig 是 extra="allow":寫錯鍵名不會報錯而是靜默忽略。
        # 寫入後回讀斷言,防止未來 graphrag 版本改鍵名時靜默壞掉。
        check = yaml.safe_load(settings_path.read_text())
        if check.get("input", {}).get("type") != input_file_type:
            msg = f"settings.yaml input.type patch failed: {check.get('input')}"
            raise WorkspaceInitError(msg)


class FakeInitializer:
    """單元測試用:建目錄與最小 settings.yaml,不 fork CLI。"""

    async def init(self, root: Path, input_file_type: str) -> None:
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "settings.yaml").write_text(yaml.safe_dump(
            {"input": {"type": input_file_type,
                       "file_pattern": _FILE_PATTERNS[input_file_type]}}))
```

`input.type` / `input.file_pattern` 鍵名已於 2026-08-19 對 graphrag `main` 原始碼驗證(`graphrag_input/input_config.py` 的 `InputConfig` 欄位、`text.py` 的 `TextFileReader` 預設 pattern);spec §6.5 已同步。鎖定版本若與驗證時不同,Task 1 需重新確認一次。

(注意:`yaml.safe_dump` 會重排並去掉註解 — 可接受,spec §6.2 的版本備份從第一次寫入開始建立。)

- [ ] **Step 5: 實作 service + 路由**

service 重點(完整實作,不得省略):
- `create_project(session, ..., initializer: WorkspaceInitializer)`:檢查 `can(user.role, user.is_active, Action.create_project)` → 產生 slug → insert Project + ProjectMember(role="owner")→ **`await session.flush()`**(取得 project.id,尚未 commit)→ `await initializer.init(ws_path(project.id), input_file_type)` → 成功才 `commit()`;`WorkspaceInitError` → `await session.rollback()` 後原樣往上拋。**不得 raise `HTTPException`**(Global Constraints:services 禁止 import FastAPI);由 route 層攔截轉成 500 `{"detail":"graphrag init failed"}`
- `initializer` 以 `Depends(get_initializer)` 注入,預設 `GraphragInitInitializer`;測試用 `app.dependency_overrides` 換成 `FakeInitializer`,只有 `test_create_project_runs_init_and_adds_owner` 保留真跑 CLI 並標 `@pytest.mark.slow`(其餘專案測試用 fake,避免每個測試 fork 一次 CLI)
- workspace 路徑:`Path(settings.workspaces_dir) / str(project.id)`;所有檔案操作前 `resolve()` 並斷言 `is_relative_to(Path(settings.workspaces_dir).resolve())`(spec §10 path traversal 防護)
- `delete_project`:`shutil.rmtree` workspace;成員級聯刪除靠 FK `ondelete="CASCADE"`
- 成員:owner 的 member row 不可被 `remove_member`/降級(400);`set_member` upsert
- 寫入操作 `audit(...)`:`project.created` / `project.updated` / `project.deleted` / `member.added` / `member.role_changed` / `member.removed`

路由 schemas:`ProjectIn{name, description?, input_file_type: Literal["text","csv","json"]}`、`ProjectOut{id,name,slug,description,input_file_type,owner_id,created_at}`、`MemberOut{user_id,email,display_name,role}`;每個端點先 `get_project_role()` 再 `can()`。

- [ ] **Step 6: 跑測試** `uv run pytest tests/test_projects.py -v` → PASS(僅 `@pytest.mark.slow` 的測試真跑 `graphrag init`,其餘用 FakeInitializer;`pyproject.toml` 加 `markers = ["slow: forks real graphrag CLI"]` 註冊 marker)

- [ ] **Step 7: Commit** `feat(backend): projects CRUD with members, permission matrix, graphrag init workspace`

---

### Task 7: 前端 scaffold + 登入 + auth store + refresh 攔截器

**Files:**
- Create: `frontend/`(vite scaffold)、`src/api/{client.ts,types.ts}`、`src/stores/auth.ts`、`src/components/{Layout.tsx,ProtectedRoute.tsx}`、`src/pages/Login.tsx`、`src/App.tsx`、`src/pages/__tests__/Login.test.tsx`

**Interfaces:**
- Consumes: Task 3-6 的 REST API
- Produces:
  - `api/client.ts`:`export async function api(path: string, init?: RequestInit): Promise<Response>` — 自動帶 `Authorization`;401 時自動 `POST /api/auth/refresh` 重試一次(用 store 的 refresh token 旋替);仍 401 → 清 session 導向 `/login`
  - `stores/auth.ts`(zustand):`{user, accessToken, refreshToken, login(), logout(), refresh()}`;refresh token 存 localStorage(`grui_refresh`)
  - `api/types.ts`:`User`、`Project`、`Member` 與 spec 欄位同名

- [ ] **Step 1: scaffold**

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend && npm i antd @tanstack/react-query react-router-dom zustand
npm i -D vitest @testing-library/react @testing-library/user-event \
     @testing-library/jest-dom jsdom
```

`vite.config.ts` 加 `server.proxy = { "/api": "http://localhost:8000" }` 與 `test: { environment: "jsdom", globals: true, setupFiles: "./src/setupTests.ts" }`;`src/setupTests.ts` 內 `import "@testing-library/jest-dom"`;package.json scripts 加 `"test": "vitest run"`。

- [ ] **Step 2: 寫失敗測試** `src/pages/__tests__/Login.test.tsx`

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import Login from "../Login";

test("submits credentials and shows error on 401", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "unauthorized" }), { status: 401 }))
  vi.stubGlobal("fetch", fetchMock)
  // Login 內用 useNavigate() → 沒有 Router 會直接 throw
  render(<MemoryRouter><Login /></MemoryRouter>)
  // label 要對上實作的中文文案,不是 /email/i
  await userEvent.type(screen.getByLabelText("電子郵件"), "a@b.c")
  await userEvent.type(screen.getByLabelText("密碼"), "wrong")
  await userEvent.click(screen.getByRole("button", { name: /登入/ }))
  await waitFor(() => expect(screen.getByText(/登入失敗/)).toBeInTheDocument())
})
```

- [ ] **Step 3: 跑測試確認失敗**(`npm test` → 找不到 Login 模組)

- [ ] **Step 4: 實作**

`api/types.ts`:

```ts
export interface User {
  id: string; email: string; display_name: string;
  role: "admin" | "user"; is_active: boolean; must_change_password: boolean;
}
export interface Project {
  id: string; name: string; slug: string; description: string | null;
  input_file_type: "text" | "csv" | "json"; owner_id: string; created_at: string;
}
export interface Member { user_id: string; email: string; display_name: string; role: string }
```

`stores/auth.ts`:

```ts
import { create } from "zustand";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  bootstrapping: boolean;          // 重整後恢復 session 期間為 true
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<string | null>;
  restore: () => Promise<void>;
}
const REFRESH_KEY = "grui_refresh";

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  bootstrapping: true,
  login: async (email, password) => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) return false;
    const body = await r.json();
    localStorage.setItem(REFRESH_KEY, body.refresh_token);
    set({ user: body.user, accessToken: body.access_token });
    return true;
  },
  logout: async () => {
    const t = localStorage.getItem(REFRESH_KEY);
    if (t) await fetch("/api/auth/logout", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: t }) });
    localStorage.removeItem(REFRESH_KEY);
    set({ user: null, accessToken: null });
  },
  refresh: async () => {
    const t = localStorage.getItem(REFRESH_KEY);
    if (!t) return null;
    const r = await fetch("/api/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: t }) });
    if (!r.ok) { localStorage.removeItem(REFRESH_KEY); set({ user: null, accessToken: null }); return null; }
    const body = await r.json();
    localStorage.setItem(REFRESH_KEY, body.refresh_token);
    set({ accessToken: body.access_token });
    return body.access_token;
  },
  restore: async () => {
    // /auth/refresh 只回 token,不回 user;少了這步,ProtectedRoute 會因為
    // user === null 把有效 session 踢回 /login
    const token = await get().refresh();
    if (!token) { set({ bootstrapping: false }); return; }
    const r = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
    set({ user: r.ok ? await r.json() : null, bootstrapping: false });
  },
}));
```

`api/client.ts`(401 重試一次):

```ts
import { useAuth } from "../stores/auth";

// refresh 是輪替式的:多個請求同時 401 各自去 refresh,第一個成功後
// 其餘拿著已作廢的 token → 全部 401 → 使用者被登出。頁面載入時通常就有
// 3-4 個並行請求,所以這個 single-flight 是必要的,不是最佳化。
let inflight: Promise<string | null> | null = null;

function refreshOnce(): Promise<string | null> {
  if (!inflight) {
    inflight = useAuth.getState().refresh().finally(() => { inflight = null });
  }
  return inflight;
}

export async function api(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const token = useAuth.getState().accessToken ?? (await refreshOnce());
  const r = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (r.status === 401 && !retried) {
    const fresh = await refreshOnce();
    if (fresh) return api(path, init, true);
  }
  return r;
}
```

已知取捨:refresh token 存 localStorage,XSS 之下等於長期憑證外洩。內部工具可接受;若之後要收斂,改成 httpOnly cookie + CSRF token。

`pages/Login.tsx`:AntD `Form`+`Input`+`Button`,label 「電子郵件」「密碼」,失敗顯示 `Alert`「登入失敗」;成功後 `must_change_password` 為 true 時 Modal 強制改密碼(POST `/api/auth/change-password`),完成後 `navigate("/")`。

`components/ProtectedRoute.tsx`:`bootstrapping → <Spin />`(**不能在恢復完成前就導向 /login**),之後 `user == null → <Navigate to="/login" />`;`components/Layout.tsx`:AntD `Layout` 側欄(專案、管理者-使用者(僅 admin)+登出)。

`App.tsx`:

```tsx
<QueryClientProvider client={new QueryClient()}>
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        <Route path="/admin/users" element={<AdminUsers />} />
      </Route>
    </Routes>
  </BrowserRouter>
</QueryClientProvider>
```

(Projects/ProjectDetail/AdminUsers 先放最小佔位組件——「Phase 後續實作」文案;Task 8/9 填實。啟動時 `App` 內 `useEffect(() => { useAuth.getState().restore() }, [])` 恢復 session — 呼叫 `restore()` 而非 `refresh()`,後者拿不到 user。)

另外:`must_change_password` 為真時後端會擋住其他端點(403),所以 Login 成功後的強制改密碼 Modal 不是裝飾,是必經流程。改完後**既有 access token 即可繼續用**(deps 每次從 DB 讀 `must_change_password`,token 內沒有此 claim);但 refresh 已全數撤銷,access 過期(15 分鐘)後需重新登入。

- [ ] **Step 5: 跑測試** `npm test` → PASS(1 test)

- [ ] **Step 6: Commit** `feat(frontend): scaffold with auth flow, refresh interceptor, protected routes`

---

### Task 8: 專案列表/建立/刪除 + 成員管理 + 專案詳情殼

**Files:**
- Create: `src/pages/Projects.tsx`、`src/pages/ProjectDetail.tsx`、`src/pages/__tests__/Projects.test.tsx`
- Modify: `src/api/client.ts`(+便捷函式 `getJSON`/`postJSON`,可選)

**Interfaces:**
- Consumes: Task 6 API、Task 7 `api()`/types
- Produces: 專案頁(表格:name、input_file_type、建立時間、擁有者;建立 Modal:name/description/input_file_type Select;刪除 Popconfirm)、詳情頁(六個 tab:Overview 顯示 meta + 成員管理表格;Files/Settings/Jobs/Query/Explore 顯示 `Tabs` disabled +「後續階段開放」`Empty`)

- [ ] **Step 1: 寫失敗測試**(mock `api` 模組)

```tsx
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";

vi.mock("../../api/client", () => ({
  api: vi.fn().mockResolvedValue(new Response(JSON.stringify([
    { id: "p1", name: "Research Corpus", slug: "research-corpus", description: null,
      input_file_type: "text", owner_id: "u1", created_at: "2026-08-19T00:00:00Z" },
  ]), { status: 200 })),
}))

test("renders project list", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><Projects /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument()
})
```

- [ ] **Step 2: 確認失敗** → **Step 3: 實作兩個頁面**(TanStack Query `useQuery(["projects"], ...)`、`useMutation` + `invalidateQueries`;成員管理同 pattern 打 `/api/projects/{id}/members`;刪除成功後 refetch;403/錯誤以 AntD `message.error` 呈現)→ **Step 4: `npm test` PASS** → **Step 5: Commit** `feat(frontend): projects list/create/delete, members, detail shell`

---

### Task 9: 管理者使用者管理頁

**Files:**
- Create: `src/pages/AdminUsers.tsx`、`src/pages/__tests__/AdminUsers.test.tsx`

**Interfaces:**
- Consumes: Task 4 API
- Produces: 表格(email、display_name、role、is_active)+ 建立/編輯 Modal + 重設密碼 + 停用;僅 admin 可見(路由已由 Layout 側欄隱藏 + 後端 403 雙保險)

- [ ] **Step 1: 寫失敗測試**(同 Task 8 模式,mock 回兩個使用者,斷言表格渲染兩列 email)— **Step 2: 確認失敗** — **Step 3: 實作**(同 Task 8 pattern;`PATCH` 與 `reset-password` mutation)— **Step 4: `npm test` PASS** — **Step 5: Commit** `feat(frontend): admin users page`

---

### Task 10: 部署 — docker-compose + Helm chart

**Files:**
- Create: `docker-compose.yml`、`frontend/Dockerfile`、`frontend/nginx.conf`、`deploy/helm/graphrag-ui/{Chart.yaml,values.yaml,templates/*}`、`deploy/helm/graphrag-ui/templates/NOTES.txt`

**Interfaces:**
- Consumes: Task 1 `backend/Dockerfile`、Task 7-9 前端 build
- Produces: `docker compose up` 可跑的全套;`helm lint` + `helm template` 通過的 chart

- [ ] **Step 1: 前端 Dockerfile + nginx**

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
```

`nginx.conf`:

```nginx
server {
  listen 80;
  location /api/ {
    proxy_pass http://api:8000;
    proxy_buffering off;            # SSE(spec §8.1)
    proxy_read_timeout 3600s;
  }
  location / { root /usr/share/nginx/html; try_files $uri /index.html; }
}
```

- [ ] **Step 2: docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: graphrag
      POSTGRES_PASSWORD: graphrag
      POSTGRES_DB: graphrag
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:                      # api 啟動時要跑 alembic,必須等 PG 真的 ready
      test: ["CMD-SHELL", "pg_isready -U graphrag"]
      interval: 3s
      timeout: 3s
      retries: 20
  api:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://graphrag:graphrag@postgres:5432/graphrag
      WORKSPACES_DIR: /data/workspaces
      JWT_SECRET: ${JWT_SECRET:?set in .env}
      BOOTSTRAP_ADMIN_EMAIL: ${BOOTSTRAP_ADMIN_EMAIL:?}
      BOOTSTRAP_ADMIN_PASSWORD: ${BOOTSTRAP_ADMIN_PASSWORD:?}
    volumes: [workspaces:/data/workspaces]
    depends_on:
      postgres: { condition: service_healthy }
  web:
    build: ./frontend
    ports: ["8080:80"]
    depends_on: [api]
volumes:
  pgdata:
  workspaces:
```

backend 容器啟動時的 `alembic upgrade head` 已寫在 Task 1 的 Dockerfile CMD 裡(連同 `--factory` 與 `ENV PATH`),這裡不用再改。

- [ ] **Step 3: Helm chart**

`Chart.yaml`:`apiVersion: v2`、`name: graphrag-ui`、`version: 0.1.0`、dependencies:`bitnami/postgresql`(condition `postgresql.enabled`)。
`values.yaml`(完整列出並註解):

```yaml
api:
  image: {}            # repository/tag
  replicas: 1          # 固定 1(spec §8.2)
  strategy: Recreate
  terminationGracePeriodSeconds: 120
  resources: {}        # 註解:indexing 子程序與查詢快取共用同一 limit;Phase 3 實測後給建議值
web: { image: {}, replicas: 2 }
ingress:
  enabled: false
  annotations:         # SSE:nginx.ingress.kubernetes.io/proxy-buffering: "off"
    {}
externalDatabase:
  url: ""              # 例 postgresql+asyncpg://user:pw@host:5432/graphrag
postgresql:
  enabled: true        # false 時必須設定 externalDatabase.url
  auth: { username: graphrag, password: "", database: graphrag }
persistence: { size: 50Gi }
jwtSecret: ""          # --set 或 existingSecret
bootstrapAdmin: { email: "", password: "" }
```

templates:`api-deployment.yaml`(env 從 values/secret 組出五個 Global Constraints 變數;`strategy: Recreate`;PVC 掛 `/data/workspaces`)、`api-service.yaml`(80→8000)、`web-deployment.yaml`+`web-service.yaml`、`ingress.yaml`(annotations 含 proxy-buffering off 與長 read timeout)、`pvc.yaml`、`secret.yaml`(jwt/bootstrap)。NOTES.txt 說明首次登入與 bootstrap admin。

- [ ] **Step 4: 驗證**

```bash
helm lint deploy/helm/graphrag-ui
helm template deploy/helm/graphrag-ui > /dev/null
helm template deploy/helm/graphrag-ui --set postgresql.enabled=false \
  --set externalDatabase.url=postgresql+asyncpg://u:p@h:5432/d \
  | grep -q 'postgresql+asyncpg://u:p@h:5432/d' && echo "external DB wired"
```

(不要 grep `externalDatabase` — 那是 values 的 key,不會出現在 render 出來的 manifest 裡,`grep -c` 會回 0 並以 exit 1 讓步驟看起來失敗。)

Expected: lint 0 errors; 內建/外部 DB 兩種 render 都成功,外部 DB 那次印出 `external DB wired`。

- [ ] **Step 5: CI**

`.github/workflows/ci.yml`:三個 job — `backend`(`uv sync --dev` → `uv run ruff check` → `uv run pytest`,testcontainers 需要 docker,GitHub runner 內建)、`frontend`(`npm ci` → `npm test` → `npm run build`)、`helm`(`helm lint` + 兩種 `helm template`)。11 個 task 都以綠燈測試收尾,沒有 CI 的話回歸只會在最後一次才發現。

- [ ] **Step 6: Commit** `feat(deploy): docker-compose, helm chart, and CI`

---

### Task 11: Smoke + smell 檢查收尾

**Files:**
- Modify: 視 smell 檢查結果

**Interfaces:**
- Consumes: 全部前序 task
- Produces: 部署可用的 Foundation-A;spec §13 #3 已記錄鎖定版本

- [ ] **Step 1: 全套 smoke**

```bash
cp .env.example .env  # 填 JWT_SECRET、BOOTSTRAP_*
docker compose up --build -d
curl -sf localhost:8080/api/ready
```

手動走:瀏覽器 `localhost:8080` → 登入 bootstrap admin → 強制改密碼 → 建立 text 專案 → 專案列表/詳情出現 → 建立一般使用者 → 該使用者登入後看不見 admin 選單、非成員專案 403 → admin 停用該使用者 → 其 session 失效。

- [ ] **Step 2: 後端+前端測試全綠**

```bash
cd backend && uv run pytest -v
cd ../frontend && npm test
```

- [ ] **Step 3: Smell 檢查(spec §9 流程)**

逐一檢視(每條都必須無輸出):

```bash
grep -rn "import graphrag\|from graphrag" backend/src/graphrag_ui/{domain,services}
grep -rn "fastapi\|sqlalchemy" backend/src/graphrag_ui/domain
grep -rn "fastapi\|HTTPException" backend/src/graphrag_ui/services   # services 禁止 import FastAPI
find backend/src frontend/src -name '*.py' -o -name '*.ts*' \
  | xargs wc -l | awk '$1 > 400 && $2 != "total"'                     # 單檔 >400 行
```

services 重複的 DB/權限樣式抽 `api/deps.py` helper;刪除未用代碼。發現即修,重跑測試。

- [ ] **Step 4: Commit** `chore: foundation-a smell review fixes`

---

## Self-Review 記錄

- **Spec coverage:** §8.3 五個環境變數(Task 1/10)、§8.4 token 策略與 bootstrap(Task 3)、§5 users/projects/members/audit_log 四表與權限矩陣(Task 2/4/5/6;jobs/settings_versions 屬後續階段)、§3 graphrag init workspace(Task 6)、§6.5 input_file_type 建立時鎖定(Task 6)、§8.1/8.2 部署(Task 10)、§9 smell 流程(Task 11)、§13 #3 版本鎖定(Task 1)。Foundation-B 範圍(上傳、設定編輯器、.env、dry-run)不在本計畫 — 正確。
- **Placeholder scan:** Task 2 Step 3 迴圈內刪表語句已內聯寫明完整寫法;無 TBD/TODO。
- **Type consistency:** `can(user_role, is_active, action, project_role)` 於 Task 5 定義、Task 6 使用;`WorkspaceInitializer.init(root, input_file_type)` 為 async,Task 6 內自洽;`rotate_refresh` 回 `(user_id, new_refresh)`,Interfaces 與實作一致;前端 `User/Project/Member` 欄位與後端 schemas 對齊。

## 計畫審查後的修訂(2026-08-19)

同日對本計畫做過一次審查,以下是已併入的修正,執行時請留意這些是**刻意的**寫法:

| 區域 | 修正 |
|---|---|
| 測試基礎設施 | `ASGITransport` 不觸發 lifespan → 改用 `LifespanManager`;新增 autouse 的 `clean_db`(TRUNCATE,表清單由 `Base.metadata` 推導);`adapters/db.py` 改 lazy engine + `reset_engine()`,模組級 engine 會在測試設 `DATABASE_URL` 前就綁到錯的 DB |
| 容器 | Dockerfile 加 `ENV PATH=/app/.venv/bin:$PATH`(否則 `graphrag` CLI 在容器內找不到,建立專案必失敗)、兩段 `uv sync`(`--no-install-project` 先裝依賴)、`--factory` 啟動(main.py 沒有模組級 `app`)、CMD 內含 `alembic upgrade head`;compose 加 PG healthcheck |
| Auth | refresh token 改標記 `revoked_at` 而非刪除,支援**重用偵測**;新增 `GET /api/auth/me`(前端恢復 session 必需);`must_change_password` 由後端強制(403),測試 helper 一律先 `_activate` 換密碼;login 加速率限制 |
| 交易邊界 | `audit()` 不再自己 commit;`create_project` 改為 flush → init → commit,失敗 rollback,並以 `WorkspaceInitError` 取代 `HTTPException`(services 禁止 import FastAPI) |
| graphrag 設定 | 鍵名為 `input.type`(不是 `input.file_type` — `InputConfig` 無此欄位且 `extra="allow"` 會靜默忽略;已對原始碼驗證並加寫入後回讀斷言);`file_pattern` 是 regex 不是 glob,text 對 `.*\.(txt\|md)$`;`graphrag init` 以 `asyncio.to_thread` 呼叫,不阻塞 event loop |
| 前端 | `api()` 加 refresh single-flight(輪替式 token 並行 refresh 會互相作廢);`restore()` + `bootstrapping` 狀態取代裸 `refresh()` |
| 測試品質 | Task 5 的 admin splat 會讓 24 個 case 用錯角色斷言,已移除;`assert "text" in yaml_text` 改為解析 YAML 斷言 `input.type`;`users[1]` 改以 email 查找;Task 4 真的去查 `audit_log` 表 |
| 流程 | Task 2 補開發用 Postgres;Task 10 補 CI;Task 11 的 smell grep 補 services 層與單檔行數檢查 |
