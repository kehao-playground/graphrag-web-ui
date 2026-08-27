"""Proxy-auth mode (spec 2026-08-27): settings, provisioning, resolver, routes."""

import asyncio

import pytest
from sqlalchemy import func, select, text

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import User
from graphrag_ui.config import Settings, get_settings
from graphrag_ui.services.auth import (
    UNUSABLE_PASSWORD_HASH,
    get_or_provision_user,
    verify_password,
)


def test_proxy_mode_requires_32_char_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="short")


def test_proxy_mode_rejects_empty_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="")


def test_local_mode_allows_empty_secret():
    # Default deployments keep today's behavior: no secret needed (spec §4)
    assert Settings(auth_mode="local", proxy_auth_secret="").auth_mode == "local"


def test_proxy_mode_accepts_32_char_secret():
    s = Settings(auth_mode="proxy", proxy_auth_secret="x" * 32)
    assert s.auth_mode == "proxy"


def test_proxy_admin_set_lowercases_strips_and_dedupes():
    s = Settings(proxy_admin_emails="A@Ex.COM, b@ex.com , ,")
    assert s.proxy_admin_set == frozenset({"a@ex.com", "b@ex.com"})


def test_proxy_admin_set_empty_default():
    assert Settings().proxy_admin_set == frozenset()


# ---- get_or_provision_user (spec §5.2) ----

async def test_provision_new_user_defaults(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = await get_or_provision_user(db_session, "new@ex.com", "New")
    assert user.email == "new@ex.com"
    assert user.role == "user"
    assert user.is_active and not user.must_change_password
    assert user.display_name == "New"
    # Unusable hash: no password ever verifies against a JIT row (spec §5.2)
    assert user.password_hash == UNUSABLE_PASSWORD_HASH
    assert not verify_password("anything", user.password_hash)


async def test_provision_listed_email_is_admin(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "root@ex.com")
    get_settings.cache_clear()
    user = await get_or_provision_user(db_session, "Root@EX.com", "Root")
    assert user.role == "admin"
    assert user.email == "root@ex.com"  # stored lowercased


async def test_existing_row_matched_case_insensitively_keeps_role(db_session):
    # create_user stores EmailStr's normalization: local part keeps its case
    db_session.add(User(email="Alice@Example.com", password_hash="x",
                        display_name="Alice", role="admin"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "alice@example.com", "Alice")
    assert user.role == "admin"  # kept, not duplicated
    assert (await db_session.execute(
        select(func.count()).select_from(User))).scalar_one() == 1


async def test_admin_reconcile_promotes_existing_user(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "boss@ex.com")
    get_settings.cache_clear()
    db_session.add(User(email="boss@ex.com", password_hash="x", display_name="Boss"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "boss@ex.com", "Boss")
    assert user.role == "admin"


async def test_admin_reconcile_never_demotes(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    db_session.add(User(email="other@ex.com", password_hash="x",
                        display_name="O", role="admin"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "other@ex.com", "O")
    assert user.role == "admin"


async def test_concurrent_first_provision_single_row(migrated_db, monkeypatch):
    # First page load fires 3-4 parallel requests (spec §5.2): all miss the
    # SELECT, all INSERT, unique index makes all but one raise. The retry
    # must converge on one row with no error surfaced.
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()

    async def one() -> None:
        engine = make_engine(migrated_db)
        factory = make_session_factory(engine)
        async with factory() as s:
            await get_or_provision_user(s, "race@ex.com", "R")
        await engine.dispose()

    await asyncio.gather(*(one() for _ in range(4)))

    engine = make_engine(migrated_db)
    async with engine.begin() as conn:
        n = (await conn.execute(
            text("select count(*) from users where email = 'race@ex.com'"))).scalar_one()
    await engine.dispose()
    assert n == 1
