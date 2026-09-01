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
from graphrag_ui.domain.role_catalog import (
    ROLE_ID_EDITOR,
    ROLE_ID_MAINTAINER,
    ROLE_ID_OPS,
    ROLE_ID_OWNER,
    ROLE_ID_USER_ADMIN,
    ROLE_ID_VIEWER,
)
from graphrag_ui.main import create_app

# pydantic EmailStr's email-validator rejects .local as a special-use
# domain (RFC 6762 mDNS) outright — verified for every release up to 2.3.0.
# The tests' admin@test.local needs that domain, so relax it for tests only
# (re-imported via `from . import` at validate time, so patching the module
# attribute works); production keeps strict validation.

email_validator.SPECIAL_USE_DOMAIN_NAMES = [
    d for d in email_validator.SPECIAL_USE_DOMAIN_NAMES if d != "local"
]


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


# The built-in role rows must survive every clean_db truncate (the R1 seed
# runs once per session; tasks 2+ read the catalog in every fixture). Same
# descriptions as the R1 migration — keep the two in sync.
_BUILTIN_ROLES_SQL = text(f"""
    INSERT INTO roles (id, scope, name, description, permissions,
                       is_system, created_at) VALUES
      ('{ROLE_ID_USER_ADMIN}', 'global', 'user_admin',
       'Manage users and roles', ARRAY['users:manage'], true, now()),
      ('{ROLE_ID_OPS}', 'global', 'ops', 'Operate every project',
       ARRAY['projects:view_any', 'projects:act_any'], true, now()),
      ('{ROLE_ID_VIEWER}', 'project', 'viewer', 'Read-only access',
       ARRAY['project:view'], true, now()),
      ('{ROLE_ID_MAINTAINER}', 'project', 'maintainer',
       'Curate documents and run indexing',
       ARRAY['project:view', 'project:edit_content', 'project:run_jobs'],
       true, now()),
      ('{ROLE_ID_EDITOR}', 'project', 'editor',
       'Maintainer plus settings and API keys',
       ARRAY['project:view', 'project:edit_content', 'project:run_jobs',
             'project:edit_settings'], true, now()),
      ('{ROLE_ID_OWNER}', 'project', 'owner', 'Full control of the project',
       ARRAY['project:view', 'project:edit_content', 'project:run_jobs',
             'project:edit_settings', 'project:manage'], true, now())
    ON CONFLICT (id) DO NOTHING
""")


@pytest.fixture
async def clean_db(migrated_db):
    """Truncate all tables before every test.

    Tests using `client` repeatedly create the same emails
    (alice@test.local...); without this isolation the second test would
    hit a unique constraint. The table list derives from Base.metadata,
    so later tasks adding models need no change here.
    """
    engine = make_engine(migrated_db)
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        await conn.execute(_BUILTIN_ROLES_SQL)
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
    """App instance for tests — lets tests set app.dependency_overrides (e.g. swap in a FakeInitializer)."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-123")
    # Test secret of >=32 bytes — anything shorter trips PyJWT InsecureKeyLengthWarning
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcd")
    # Disable the runner loop for every app test: the lifespan starts it, and
    # the default cap of 2 would auto-execute queued jobs against the real
    # graphrag CLI. Runner-loop tests call _execute/run_loop directly with
    # their own MAX_CONCURRENT_JOBS override.
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    get_settings.cache_clear()
    await reset_engine()  # env changed; the shared engine must be rebuilt
    auth_routes._LOGIN_FAILURES.clear()  # module-level rate limiting leaks across tests (same bucket accumulating -> 429)
    return create_app()


@pytest.fixture
async def client(app):
    # httpx's ASGITransport does **not** trigger lifespan. Without
    # LifespanManager, bootstrap_admin() never runs and
    # app.state.graphrag_version is missing -> every later task's login
    # tests would 401.
    async with (
        LifespanManager(app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()
