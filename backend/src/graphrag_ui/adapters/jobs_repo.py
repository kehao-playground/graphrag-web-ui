"""PG queue operations for indexing jobs (spec §6.3). All functions commit
their own transaction except insert_job (caller owns enqueue semantics)."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, Result, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Job, TestRun
from graphrag_ui.domain.jobs import TERMINAL_STATUSES

_ACTIVE = ("queued", "running")

logger = logging.getLogger(__name__)


async def insert_job(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    type: str,
    method: str,
    argv: list[str],
    queued_by: uuid.UUID,
) -> Job:
    job = Job(project_id=project_id, type=type, method=method, argv=argv, queued_by=queued_by)
    session.add(job)
    await session.flush()
    return job


async def claim_next(session: AsyncSession, worker_id: str) -> Job | None:
    row = (
        await session.execute(
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.queued_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.commit()  # release the FOR UPDATE scan
        return None
    row.status = "running"
    row.worker_id = worker_id
    row.started_at = func.now()
    row.heartbeat_at = func.now()
    await session.commit()
    # func.now() assignments expire the attributes in memory (values live
    # server-side); AsyncSession cannot lazy-load on attribute access.
    await session.refresh(row)
    return row


async def heartbeat(
    session: AsyncSession, job_id: uuid.UUID, worker_id: str, pid: int | None = None
) -> None:
    values: dict = {"heartbeat_at": func.now(), "worker_id": worker_id}
    if pid is not None:
        values["pid"] = pid
    await session.execute(update(Job).where(Job.id == job_id).values(**values))
    await session.commit()
    await _reload(session, job_id)


async def set_progress(session: AsyncSession, job_id: uuid.UUID, done: int, total: int) -> None:
    """Batch progress tick. Reassigns the whole JSONB value — the column has
    no mutation tracking, so an in-place edit would never flush."""
    await session.execute(
        update(Job).where(Job.id == job_id).values(progress={"done": done, "total": total})
    )
    await session.commit()
    await _reload(session, job_id)


async def request_cancel(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """A queued job is finished as cancelled on the spot — no claim, no
    snapshot, no epoch bump, no spawn (R2-09). A running job only gets the
    flag its runner polls. claim_next locks the queued row, so a cancel
    racing a claim re-evaluates against `running` and takes the second path."""
    res = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "queued")
        .values(status="cancelled", cancel_requested_at=func.now(), finished_at=func.now())
    )
    if _rowcount(res) == 1:
        await _close_test_run(session, job_id)
    else:
        res = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(cancel_requested_at=func.now())
        )
    await session.commit()
    # The update leaves identity-map instances expired; AsyncSession cannot
    # lazy-load on later attribute access, so reload explicitly.
    await _reload(session, job_id)
    return _rowcount(res) == 1


async def finish(
    session: AsyncSession,
    job_id: uuid.UUID,
    status: str,
    *,
    exit_code: int | None = None,
    error: str | None = None,
    stats: dict | None = None,
    on_before_commit: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> bool:
    """Write the terminal state, guarded on the row still being active.

    Returns False — and skips on_before_commit — when the row is already
    terminal: reconcile_stale finished it under a slow worker, whose result
    is then discarded by design (R1-77). A failing on_before_commit does not
    take the terminal write down with it (R1-90): the status is re-written
    alone, with the promotion failure recorded in `error`."""
    if status not in TERMINAL_STATUSES:
        msg = f"non-terminal finish status: {status}"
        raise ValueError(msg)
    values = {"status": status, "exit_code": exit_code, "error": error, "stats": stats}
    if not await _terminal_update(session, job_id, values):
        # Nothing was written; commit (not rollback) so the caller's
        # instances are not expired under an AsyncSession.
        await session.commit()
        logger.warning("job %s was already terminal; %s result discarded", job_id, status)
        await _reload(session, job_id)
        return False
    if on_before_commit is not None:
        # Baseline promotion must land in the SAME transaction that marks the
        # job succeeded (spec 5.2): otherwise a crash between the two leaves
        # a project pointing at a baseline for a job that never finished.
        try:
            await on_before_commit(session)
        except Exception as exc:
            logger.exception("post-finish hook failed for job %s", job_id)
            await session.rollback()
            note = f"baseline promotion failed: {exc!r}"
            values["error"] = f"{error}\n{note}" if error else note
            if not await _terminal_update(session, job_id, values):
                await session.commit()
                await _reload(session, job_id)
                return False
    await session.commit()
    await _reload(session, job_id)
    return True


async def _terminal_update(session: AsyncSession, job_id: uuid.UUID, values: dict) -> bool:
    res = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(_ACTIVE))
        .values(**values, finished_at=func.now())
    )
    if _rowcount(res) == 0:
        return False
    await _close_test_run(session, job_id)
    return True


async def _close_test_run(session: AsyncSession, job_id: uuid.UUID) -> None:
    """A test_run job's run closes with the job, however the job ended: a
    failed preamble, a queued cancel or a reconcile never reach the worker's
    own stamp, and the workbench polls while finished_at is null (R2-12).
    A no-op for index/update jobs, which own no run."""
    await session.execute(
        update(TestRun)
        .where(TestRun.job_id == job_id, TestRun.finished_at.is_(None))
        .values(finished_at=func.now())
    )


def _rowcount(res: Result) -> int:
    # rowcount is a CursorResult attribute; the execute() return type is the
    # Result base, which does not declare it.
    return cast(CursorResult, res).rowcount


async def _reload(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Re-SELECT the row so identity-map instances hold server-generated
    values (func.now() timestamps expire attributes after Core updates —
    AsyncSession has no lazy-load-on-attribute-access)."""
    obj = await session.get(Job, job_id)
    if obj is not None:
        await session.refresh(obj)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def list_jobs(
    session: AsyncSession, project_id: uuid.UUID, limit: int = 50, *, job_type: str | None = None
) -> list[Job]:
    query = select(Job).where(Job.project_id == project_id)
    if job_type is not None:
        query = query.where(Job.type == job_type)
    res = await session.execute(query.order_by(Job.queued_at.desc()).limit(limit))
    return list(res.scalars().all())


async def find_stale_running(session: AsyncSession, older_than: datetime) -> list[Job]:
    res = await session.execute(
        select(Job).where(Job.status == "running", Job.heartbeat_at < older_than)
    )
    return list(res.scalars().all())


async def count_running(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(Job).where(Job.status == "running"))
    ).scalar_one()


async def last_finished(session: AsyncSession, project_id: uuid.UUID) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.status == "succeeded")
            .order_by(Job.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
