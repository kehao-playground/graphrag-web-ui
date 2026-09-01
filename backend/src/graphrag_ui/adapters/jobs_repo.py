"""PG queue operations for indexing jobs (spec §6.3). All functions commit
their own transaction except insert_job (caller owns enqueue semantics)."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Job
from graphrag_ui.domain.jobs import TERMINAL_STATUSES

_ACTIVE = ("queued", "running")


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


async def request_cancel(session: AsyncSession, job_id: uuid.UUID) -> bool:
    res = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(_ACTIVE))
        .values(cancel_requested_at=func.now())
    )
    await session.commit()
    # The update leaves identity-map instances expired; AsyncSession cannot
    # lazy-load on later attribute access, so reload explicitly.
    await _reload(session, job_id)
    return res.rowcount == 1


async def finish(
    session: AsyncSession,
    job_id: uuid.UUID,
    status: str,
    *,
    exit_code: int | None = None,
    error: str | None = None,
    stats: dict | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        msg = f"non-terminal finish status: {status}"
        raise ValueError(msg)
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=status, exit_code=exit_code, error=error, stats=stats, finished_at=func.now()
        )
    )
    await session.commit()
    await _reload(session, job_id)


async def _reload(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Re-SELECT the row so identity-map instances hold server-generated
    values (func.now() timestamps expire attributes after Core updates —
    AsyncSession has no lazy-load-on-attribute-access)."""
    obj = await session.get(Job, job_id)
    if obj is not None:
        await session.refresh(obj)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def list_jobs(session: AsyncSession, project_id: uuid.UUID, limit: int = 50) -> list[Job]:
    res = await session.execute(
        select(Job).where(Job.project_id == project_id).order_by(Job.queued_at.desc()).limit(limit)
    )
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
