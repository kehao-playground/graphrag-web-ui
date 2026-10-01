import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from graphrag_ui.adapters.jobs_repo import (
    claim_next,
    count_running,
    find_stale_running,
    finish,
    get_job,
    heartbeat,
    insert_job,
    last_succeeded,
    list_jobs,
    request_cancel,
    set_progress,
)
from graphrag_ui.adapters.models import Project, User


async def _mk_project(session, email="owner@t.local"):
    u = User(email=email, password_hash="x", display_name="o")
    session.add(u)
    await session.flush()
    p = Project(name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id, input_file_type="text")
    session.add(p)
    await session.flush()
    return p, u


async def _insert(session, p, u, type_="index"):
    return await insert_job(
        session,
        project_id=p.id,
        type=type_,
        method="standard",
        argv=["index", "--root", "/ws", "--method", "standard"],
        queued_by=u.id,
    )


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


async def test_finish_and_last_succeeded(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    await finish(db_session, j.id, "succeeded", exit_code=0, stats={"num_documents": 3})
    got = await get_job(db_session, j.id)
    assert got.status == "succeeded" and got.stats["num_documents"] == 3
    assert got.finished_at is not None
    lf = await last_succeeded(db_session, p.id)
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
    stale = await find_stale_running(db_session, datetime.now(UTC) + timedelta(seconds=61))
    assert [x.id for x in stale] == [j.id]


async def test_list_jobs_newest_first(db_session):
    p, u = await _mk_project(db_session)
    # The per-project mutex allows only one active job, so each run must
    # reach a terminal state before the next one can be queued.
    for n in range(3):
        j = await _insert(db_session, p, u)
        await finish(db_session, j.id, "succeeded", exit_code=0)
    jobs, total = await list_jobs(db_session, p.id)
    assert len(jobs) == 3 and total == 3
    assert jobs[0].queued_at >= jobs[-1].queued_at  # newest first


async def test_finish_never_overwrites_a_terminal_status(db_session):
    """R1-77: a slow worker returning after reconcile_stale must not flip
    failed(interrupted) back to succeeded, nor run its promotion."""
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    await finish(db_session, j.id, "failed(interrupted)", error="worker heartbeat timeout")
    promoted: list[bool] = []

    async def _promote(_sess):
        promoted.append(True)

    assert (
        await finish(db_session, j.id, "succeeded", exit_code=0, on_before_commit=_promote) is False
    )
    got = await get_job(db_session, j.id)
    assert got.status == "failed(interrupted)" and got.exit_code is None
    assert promoted == []


async def test_finish_keeps_the_outcome_when_promotion_fails(db_session):
    """R1-90: a promotion bug must not roll back the terminal write and
    leave the row for reconcile_stale to mark failed(interrupted)."""
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")

    async def _broken_promote(_sess):
        raise RuntimeError("promotion exploded")

    assert await finish(
        db_session, j.id, "succeeded", exit_code=0, on_before_commit=_broken_promote
    )
    got = await get_job(db_session, j.id)
    assert got.status == "succeeded" and got.exit_code == 0 and got.finished_at is not None
    assert "baseline promotion failed" in got.error and "promotion exploded" in got.error


async def test_cancelling_a_queued_job_finishes_it_without_a_claim(db_session):
    """R2-09: a queued job never reaches the runner once cancelled."""
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    assert await request_cancel(db_session, j.id) is True
    got = await get_job(db_session, j.id)
    assert got.status == "cancelled"
    assert got.cancel_requested_at is not None and got.finished_at is not None
    assert await claim_next(db_session, "w1") is None


async def test_cancelling_a_running_job_only_flags_it(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    assert await request_cancel(db_session, j.id) is True
    got = await get_job(db_session, j.id)
    assert got.status == "running" and got.cancel_requested_at is not None


class _Statements:
    def __init__(self, session):
        self.engine = session.bind.sync_engine
        self.seen: list[str] = []

    def _record(self, conn, cursor, statement, params, context, executemany):
        self.seen.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self.seen

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)


async def test_runner_writes_on_a_fresh_session_issue_one_statement(db_session, migrated_db):
    """R1-99: the runner's heartbeat and progress writes run on a session
    that holds no Job instance; nothing needs a reload, so nothing is
    re-SELECTed after the UPDATE."""
    from graphrag_ui.adapters.db import make_engine, make_session_factory

    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    engine = make_engine(migrated_db)
    try:
        async with make_session_factory(engine)() as fresh:
            with _Statements(fresh) as seen:
                assert await heartbeat(fresh, j.id, "w1") is False
                await set_progress(fresh, j.id, 1, 10)
            assert [s.split()[0] for s in seen] == ["UPDATE", "UPDATE"], seen
    finally:
        await engine.dispose()


async def test_heartbeat_reports_a_requested_cancel(db_session):
    """R1-99: the beat returns cancel_requested_at, so a beat replaces that
    second's separate cancel poll."""
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    assert await heartbeat(db_session, j.id, "w1") is False
    assert await request_cancel(db_session, j.id) is True
    assert await heartbeat(db_session, j.id, "w1") is True


async def test_a_loaded_instance_still_sees_the_server_values(db_session):
    p, u = await _mk_project(db_session)
    j = await _insert(db_session, p, u)
    await claim_next(db_session, "w1")
    before = j.heartbeat_at
    await heartbeat(db_session, j.id, "w1", pid=7)
    assert j.pid == 7 and j.heartbeat_at >= before
