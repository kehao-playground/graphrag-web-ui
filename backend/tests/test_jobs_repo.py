import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from graphrag_ui.adapters.jobs_repo import (
    claim_next,
    count_running,
    find_stale_running,
    finish,
    get_job,
    heartbeat,
    insert_job,
    last_finished,
    list_jobs,
    request_cancel,
)
from graphrag_ui.adapters.models import Project, User


async def _mk_project(session, email="owner@t.local"):
    u = User(email=email, password_hash="x", display_name="o")
    session.add(u)
    await session.flush()
    p = Project(name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id,
                input_file_type="text")
    session.add(p)
    await session.flush()
    return p, u


async def _insert(session, p, u, type_="index"):
    return await insert_job(
        session, project_id=p.id, type=type_, method="standard",
        argv=["index", "--root", "/ws", "--method", "standard"], queued_by=u.id)


async def test_claim_next_exclusive_and_running(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    a = await claim_next(db_session, "w1")
    assert a.id == j.id and a.status == "running" and a.worker_id == "w1"
    assert a.started_at is not None and a.heartbeat_at is not None
    assert await claim_next(db_session, "w2") is None  # nothing queued left
    assert (await count_running(db_session)) == 1


async def test_per_project_mutex_partial_index(db_session):
    p, u = await _mk_project(db_session)
    await _insert(db_session, p, u)
    await db_session.commit()  # enqueue committed; duplicate is its own txn
    with pytest.raises(IntegrityError):
        await _insert(db_session, p, u)  # second queued job for same project
    # Failed flush invalidates the transaction; PG-side the partial unique
    # index jobs_one_active_per_project is what rejected the insert.
    await db_session.rollback()
    await db_session.refresh(u)  # rollback expired u; AsyncSession can't lazy-load
    p2, _ = await _mk_project(db_session, email="o2@t.local")
    await _insert(db_session, p2, u)  # other project: fine


async def test_finish_and_last_finished(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    await finish(db_session, j.id, "succeeded", exit_code=0,
                 stats={"num_documents": 3})
    got = await get_job(db_session, j.id)
    assert got.status == "succeeded" and got.stats["num_documents"] == 3
    assert got.finished_at is not None
    lf = await last_finished(db_session, p.id)
    assert lf.id == j.id
    with pytest.raises(ValueError):
        await finish(db_session, j.id, "running")  # non-terminal rejected


async def test_cancel_and_heartbeat(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    assert await request_cancel(db_session, j.id) is True
    assert (await get_job(db_session, j.id)).cancel_requested_at is not None
    await finish(db_session, j.id, "cancelled", exit_code=-15)
    assert await request_cancel(db_session, j.id) is False  # terminal: no-op
    j2 = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    await heartbeat(db_session, j2.id, "w1", pid=4242)
    row = await get_job(db_session, j2.id)
    assert row.pid == 4242


async def test_find_stale_running(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    stale = await find_stale_running(
        db_session, datetime.now(UTC) + timedelta(seconds=61))
    assert [x.id for x in stale] == [j.id]


async def test_list_jobs_newest_first(db_session):
    p, u = await _mk_project(db_session)
    # The per-project mutex allows only one active job, so each run must
    # reach a terminal state before the next one can be queued.
    for n in range(3):
        j = await _insert(db_session, p, u)
        await finish(db_session, j.id, "succeeded", exit_code=0)
    jobs = await list_jobs(db_session, p.id)
    assert len(jobs) == 3
    assert jobs[0].queued_at >= jobs[-1].queued_at  # newest first
