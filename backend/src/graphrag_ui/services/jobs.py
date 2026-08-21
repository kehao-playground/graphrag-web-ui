"""Job use cases: enqueue with pre-checks, cancel, preflight summary.
Owns the transaction boundary; raises domain errors the API layer maps."""

import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.models import Job, Project, User
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import build_argv
from graphrag_ui.services.projects import _ws_path


class JobConflictError(RuntimeError):
    """Another queued/running job for this project (DB mutex)."""


class DiskWatermarkError(RuntimeError):
    """Free space on the workspaces volume is below the watermark."""


async def enqueue(
    session: AsyncSession, project: Project, type: str, method: str, actor: User
) -> Job:
    # Validate type/method before any I/O; build_argv raises ValueError.
    project_id = str(project.id)  # snapshot: rollback() expires instances
    root = _ws_path(project.id)
    argv = build_argv(type, method, root)
    settings = get_settings()
    # Measure the workspaces ROOT (spec §6.1), not the possibly-missing
    # project dir; create the root if needed so disk_usage has a target.
    ws_root = Path(settings.workspaces_dir).resolve()
    ws_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ws_root).free
    if free < settings.disk_watermark_mb * 1024 * 1024:
        raise DiskWatermarkError(str(free))
    try:
        # insert_job flushes; the partial unique index raises IntegrityError
        # here already when another active job exists — map both paths.
        job = await jobs_repo.insert_job(
            session, project_id=project.id, type=type, method=method, argv=argv, queued_by=actor.id
        )
        await session.commit()
    except IntegrityError:
        # jobs_one_active_per_project partial unique index fired: another
        # active job won the race. Never check-then-insert (spec §5).
        await session.rollback()
        raise JobConflictError(project_id) from None
    return job


async def cancel(session: AsyncSession, job: Job) -> bool:
    return await jobs_repo.request_cancel(session, job.id)


async def get(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await jobs_repo.get_job(session, job_id)


async def list_for_project(session: AsyncSession, project_id) -> list[Job]:
    return await jobs_repo.list_jobs(session, project_id)


async def active_job(session: AsyncSession, project_id) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.status.in_(("queued", "running")))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


async def preflight(session: AsyncSession, project: Project) -> dict:
    settings = get_settings()
    root = _ws_path(project.id)
    ws_root = Path(settings.workspaces_dir).resolve()
    last = await jobs_repo.last_finished(session, project.id)
    last_run = None
    if last is not None and last.stats:
        # stats keys may be absent on partial runs — keep fields None-safe.
        s = last.stats
        last_run = {
            "type": last.type,
            "status": last.status,
            "finished_at": last.finished_at,
            "total_runtime_seconds": s.get("total_runtime"),
            "num_documents": s.get("num_documents"),
            "update_documents": s.get("update_documents"),
        }
    return {
        "active_job": await active_job(session, project.id),
        "last_run": last_run,
        "cache_bytes": await _tree_bytes(root / "cache"),
        "cache_quota_mb": settings.cache_quota_mb,
        "disk_free_mb": shutil.disk_usage(ws_root).free // (1024 * 1024),
        "disk_watermark_mb": settings.disk_watermark_mb,
    }
