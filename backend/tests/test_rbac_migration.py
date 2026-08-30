"""R1/R2 RBAC migrations (spec §5.2): seed, backfill, and lossy downgrade.

Runs against its OWN Postgres container: the shared session fixture is
already migrated to head, and this test walks revisions up/down, which
would mutate the shared schema under other tests' feet.

Two facts about this repo shape the plumbing below — do not simplify them
away:

1. `migrations/env.py` overwrites `sqlalchemy.url` with
   `get_settings().database_url` at import time, so setting that option on
   the Config object does NOTHING. The container is selected through the
   DATABASE_URL env var plus `get_settings.cache_clear()`, and restored on
   teardown so the session-wide container keeps working.
2. No sync driver is installed (neither psycopg2 nor psycopg), so every
   statement here goes through asyncpg. The tests stay SYNC functions:
   env.py runs `asyncio.run()` internally, which raises inside a running
   loop — an `async def` test would break every alembic call. The helpers
   therefore wrap their async work in `asyncio.run()`.
"""
import asyncio
import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from graphrag_ui.adapters.db import make_engine
from graphrag_ui.config import get_settings
from graphrag_ui.domain.role_catalog import ROLE_ID_OPS

LEGACY_BASE = "47b77c99bc8f"  # indexing_jobs, the revision right before R1
# Relative refs, never "head": Task 4 adds R2, which would silently
# retarget these R1 assertions (they require the legacy columns to exist).
R1 = f"{LEGACY_BASE}+1"
R2 = f"{LEGACY_BASE}+2"


@pytest.fixture(scope="module")
def legacy_container():
    """One container for the whole module (each test resets it below).

    DATABASE_URL points at it for the module's duration because env.py
    reads the url through get_settings(); the previous value is restored
    so the session-scoped shared container survives.
    """
    previous = os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "migrations")
        yield url, cfg
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()


@pytest.fixture
def legacy_db(legacy_container):
    """The module container reset to the pre-R1 revision for each test."""
    url, cfg = legacy_container
    command.downgrade(cfg, "base")
    command.upgrade(cfg, LEGACY_BASE)
    return url, cfg


async def _aexec(url: str, statements: tuple[str, ...]) -> None:
    engine = make_engine(url)
    try:
        async with engine.begin() as conn:
            for stmt in statements:  # one statement per execute (asyncpg)
                await conn.execute(sa.text(stmt))
    finally:
        await engine.dispose()


async def _arows(url: str, sql: str) -> list:
    engine = make_engine(url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(sa.text(sql))).fetchall()
    finally:
        await engine.dispose()


def _exec(url: str, *statements: str) -> None:
    # Sync wrapper: these tests must not be async (see the module docstring)
    asyncio.run(_aexec(url, statements))


def _rows(url: str, sql: str) -> list:
    return asyncio.run(_arows(url, sql))


def _seed_legacy_rows(url):
    _exec(
        url,
        """
            INSERT INTO users (id, email, password_hash, display_name, role,
                               is_active, must_change_password, created_at)
            VALUES
              ('11111111-1111-1111-1111-111111111111', 'a@x.com', 'h', 'A',
               'admin', true, false, now()),
              ('22222222-2222-2222-2222-222222222222', 'b@x.com', 'h', 'B',
               'user', true, false, now()),
              ('33333333-3333-3333-3333-333333333333', 'c@x.com', 'h', 'C',
               'user', false, false, now()),
              ('44444444-4444-4444-4444-444444444444', 'd@x.com', 'h', 'D',
               'admin', true, false, now())
        """,
        """
            INSERT INTO projects (id, name, slug, description, owner_id,
                                  input_file_type, created_at)
            VALUES ('aaaa0000-0000-0000-0000-00000000000a', 'P', 'p', NULL,
                    '11111111-1111-1111-1111-111111111111', 'text', now())
        """,
        """
            INSERT INTO project_members (project_id, user_id, role)
            VALUES
              ('aaaa0000-0000-0000-0000-00000000000a',
               '11111111-1111-1111-1111-111111111111', 'owner'),
              ('aaaa0000-0000-0000-0000-00000000000a',
               '22222222-2222-2222-2222-222222222222', 'editor'),
              ('aaaa0000-0000-0000-0000-00000000000a',
               '44444444-4444-4444-4444-444444444444', 'viewer')
        """,
    )


def test_r1_seeds_builtins_and_backfills(legacy_db):
    url, cfg = legacy_db
    _seed_legacy_rows(url)
    command.upgrade(cfg, R1)

    roles = {r[0]: (r[1], tuple(r[2])) for r in _rows(url, """
        SELECT name, scope, permissions FROM roles ORDER BY name
    """)}
    # spot-check the six built-ins carry the spec §4.2 atom sets
    assert roles["user_admin"] == ("global", ("users:manage",))
    assert set(roles["ops"][1]) == {"projects:view_any", "projects:act_any"}
    # (array column order is not guaranteed — compare as sets)
    assert roles["viewer"] == ("project", ("project:view",))
    assert roles["editor"][0] == "project"
    assert set(roles["editor"][1]) == {
        "project:view", "project:edit_content", "project:run_jobs",
        "project:edit_settings"}
    assert set(roles["owner"][1]) == {
        "project:view", "project:edit_content", "project:run_jobs",
        "project:edit_settings", "project:manage"}
    assert set(roles["maintainer"][1]) == {
        "project:view", "project:edit_content", "project:run_jobs"}

    # users.role='admin' rows gain exactly [user_admin, ops]; 'user' rows none
    grants = _rows(url, """
        SELECT u.email, r.name FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        ORDER BY u.email, r.name
    """)
    assert grants == [
        ("a@x.com", "ops"), ("a@x.com", "user_admin"),
        ("d@x.com", "ops"), ("d@x.com", "user_admin"),
    ]

    # member strings map to role_id; the combination case (global admin
    # AND project editor on one user, spec §9) must not interfere
    members = {r[0]: r[1] for r in _rows(url, """
        SELECT u.email, r.name FROM project_members pm
        JOIN users u ON u.id = pm.user_id
        JOIN roles r ON r.id = pm.role_id
    """)}
    assert members == {
        "a@x.com": "owner", "b@x.com": "editor", "d@x.com": "viewer"}

    # R1 keeps the legacy columns (spec deviation note in plan header)
    cols = {r[0] for r in _rows(url, """
        SELECT column_name FROM information_schema.columns
        WHERE table_name IN ('users', 'project_members') AND column_name = 'role'
    """)}
    assert cols == {"role"}


def test_r1_is_safe_on_empty_database(legacy_db):
    url, cfg = legacy_db
    command.upgrade(cfg, R1)  # no legacy rows at all
    assert len(_rows(url, "SELECT id FROM roles")) == 6
    assert _rows(url, "SELECT * FROM user_roles") == []


def test_r1_downgrade_drops_rbac_tables(legacy_db):
    url, cfg = legacy_db
    _seed_legacy_rows(url)
    command.upgrade(cfg, R1)
    command.downgrade(cfg, LEGACY_BASE)
    tables = {r[0] for r in _rows(url, """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)}
    assert "roles" not in tables and "user_roles" not in tables
    # legacy data untouched by the roundtrip
    assert len(_rows(url, "SELECT id FROM users")) == 4
    members = _rows(url, "SELECT role FROM project_members ORDER BY role")
    assert [m[0] for m in members] == ["editor", "owner", "viewer"]


def test_r2_drops_columns_and_lossy_downgrade(legacy_db):
    url, cfg = legacy_db
    _seed_legacy_rows(url)
    command.upgrade(cfg, R2)
    cols = {r[0] for r in _rows(url, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users'
    """)}
    assert "role" not in cols

    # new-model state: ops-only holder + maintainer member + editor member
    _exec(
        url,
        """
            INSERT INTO users (id, email, password_hash, display_name,
                               is_active, must_change_password, created_at)
            VALUES ('55555555-5555-5555-5555-555555555555', 'e@x.com', 'h',
                    'E', true, false, now())
        """,
        f"""
            INSERT INTO user_roles (user_id, role_id) VALUES
              ('55555555-5555-5555-5555-555555555555', '{ROLE_ID_OPS}')
        """,
        # b@x.com (seeded editor) flips to maintainer (the floor path);
        # d@x.com (seeded viewer) flips to editor (the map path). UPDATE,
        # not INSERT — (project_id, user_id) is the PK.
        """
            UPDATE project_members SET role_id =
              '00000000-0000-4000-8000-000000000004'
            WHERE user_id = '22222222-2222-2222-2222-222222222222'
        """,
        """
            UPDATE project_members SET role_id =
              '00000000-0000-4000-8000-000000000005'
            WHERE user_id = '44444444-4444-4444-4444-444444444444'
        """,
    )

    command.downgrade(cfg, R1)  # back to R1: legacy columns restored
    users = dict(_rows(url, "SELECT email, role FROM users"))
    assert users["e@x.com"] == "admin"  # ops-only upgraded on purpose
    assert users["b@x.com"] == "user"
    members = dict(_rows(url, """
        SELECT u.email, pm.role FROM project_members pm
        JOIN users u ON u.id = pm.user_id
    """))
    assert members["b@x.com"] == "viewer"  # maintainer floors at viewer
    assert members["a@x.com"] == "owner"
    assert members["d@x.com"] == "editor"

    command.upgrade(cfg, R2)
