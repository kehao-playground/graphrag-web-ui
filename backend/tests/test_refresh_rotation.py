"""Refresh-token rotation: atomicity, the reuse grace window and the family
lifetime (fix wave F6: R1-68, R2-05, R2-26).
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import RefreshToken
from graphrag_ui.services import auth as auth_service


async def _login(client) -> str:
    r = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
    )
    assert r.status_code == 200
    return r.json()["refresh_token"]


async def _refresh(client, token: str):
    return await client.post("/api/auth/refresh", json={"refresh_token": token})


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _backdate(db_session, token: str, **delta: timedelta) -> None:
    """Shift a row's timestamps into the past (revoked_at / family_created_at)."""
    row = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == _h(token)))
    ).scalar_one()
    values = {col: getattr(row, col) - d for col, d in delta.items()}
    await db_session.execute(update(RefreshToken).where(RefreshToken.id == row.id).values(**values))
    await db_session.commit()


async def _row_count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(RefreshToken))).scalar_one()


# --- R1-68: one statement consumes the token ---


async def test_concurrent_presentations_rotate_exactly_once(client, db_session, migrated_db):
    """Two requests carrying the same token race past a read-then-write
    check and both rotate — a replayed token never trips reuse detection.
    A third session holds the row lock so both rotations are provably in
    flight together; afterwards exactly one successor exists and both
    callers got it (the loser via the grace window)."""
    token = await _login(client)
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as holder, factory() as s1, factory() as s2:
            await holder.execute(
                select(RefreshToken).where(RefreshToken.token_hash == _h(token)).with_for_update()
            )
            tasks = [asyncio.create_task(auth_service.rotate_refresh(s, token)) for s in (s1, s2)]
            try:
                done, _ = await asyncio.wait(set(tasks), timeout=1.0)
                assert done == set(), "a rotation did not wait for the row lock"
            finally:
                await holder.commit()
            a, b = await asyncio.gather(*tasks)
    finally:
        await engine.dispose()
    assert a is not None and b is not None
    assert a == b
    assert await _row_count(db_session) == 2  # the login token + one successor
    assert (await _refresh(client, a[1])).status_code == 200


# --- R2-05: a benign double-present inside the grace window ---


async def test_old_token_within_grace_returns_the_same_successor(client):
    """Two tabs refreshing back to back present the same token. Inside the
    grace window the second gets the successor the first got, instead of
    revoking the family and logging every tab out."""
    old = await _login(client)
    r1 = await _refresh(client, old)
    r2 = await _refresh(client, old)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["refresh_token"] == r2.json()["refresh_token"]
    assert (await _refresh(client, r1.json()["refresh_token"])).status_code == 200


async def test_old_token_after_grace_revokes_the_family(client, db_session):
    old = await _login(client)
    new = (await _refresh(client, old)).json()["refresh_token"]
    await _backdate(db_session, old, revoked_at=auth_service.REFRESH_REUSE_GRACE)
    assert (await _refresh(client, old)).status_code == 401
    assert (await _refresh(client, new)).status_code == 401


async def test_old_token_after_successor_was_used_revokes_the_family(client):
    """Grace covers a double-present, not a replay: once the successor has
    itself been rotated, the old token reappearing is reuse."""
    old = await _login(client)
    new = (await _refresh(client, old)).json()["refresh_token"]
    newer = (await _refresh(client, new)).json()["refresh_token"]
    assert (await _refresh(client, old)).status_code == 401
    assert (await _refresh(client, newer)).status_code == 401


async def test_logged_out_token_replayed_revokes_the_family(client):
    old = await _login(client)
    other = await _login(client)
    await client.post("/api/auth/logout", json={"refresh_token": old})
    assert (await _refresh(client, old)).status_code == 401
    assert (await _refresh(client, other)).status_code == 401


# --- R2-26 / D4: absolute family lifetime ---


async def test_family_older_than_its_lifetime_cannot_refresh(client, db_session):
    token = await _login(client)
    await _backdate(
        db_session,
        token,
        family_created_at=auth_service.REFRESH_FAMILY_MAX_AGE + timedelta(seconds=1),
    )
    # expires_at was computed at issue; rotation must still enforce the cap
    assert (await _refresh(client, token)).status_code == 401


async def test_rotation_never_extends_past_the_family_lifetime(client, db_session):
    token = await _login(client)
    await _backdate(
        db_session, token, family_created_at=auth_service.REFRESH_FAMILY_MAX_AGE - timedelta(days=1)
    )
    new = (await _refresh(client, token)).json()["refresh_token"]
    old_row, new_row = [
        (
            await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == _h(t)))
        ).scalar_one()
        for t in (token, new)
    ]
    assert new_row.family_id == old_row.family_id
    assert new_row.family_created_at == old_row.family_created_at
    cap = old_row.family_created_at + auth_service.REFRESH_FAMILY_MAX_AGE
    assert new_row.expires_at <= cap
    assert new_row.expires_at > datetime.now(UTC)


async def test_each_login_starts_a_new_family(client, db_session):
    await _login(client)
    await _login(client)
    families = (await db_session.execute(select(RefreshToken.family_id))).scalars().all()
    assert len(set(families)) == 2
