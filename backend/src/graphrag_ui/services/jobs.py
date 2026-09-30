"""Job use cases: enqueue with pre-checks, cancel, preflight summary.
Owns the transaction boundary; raises domain errors the API layer maps."""

import asyncio
import logging
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.models import Job, Project, User
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import build_argv
from graphrag_ui.services.audit import audit
from graphrag_ui.services.errors import CodedServiceError, JobConflictError
from graphrag_ui.services.files import QuotaExceededError, quota_bytes, usage_bytes
from graphrag_ui.services.fs_stats import MIB, tree_bytes, workspaces_free_bytes
from graphrag_ui.services.project_lock import active_job, lock_project
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.settings import check_workspace_settings

logger = logging.getLogger(__name__)


class DiskWatermarkError(CodedServiceError, RuntimeError):
    """Free space on the workspaces volume is below the watermark."""

    code = "disk_watermark"


class ProjectOverQuotaError(QuotaExceededError):
    """input/ + output/ already above the project quota at enqueue (spec §10):
    an index would only grow output/. The upload path's error, answered 409
    rather than 413 — the request is not what is too large."""


async def enqueue(
    session: AsyncSession, project: Project, type: str, method: str, actor: User
) -> Job:
    # Validate type/method before any I/O; build_argv raises ValueError.
    project_id = str(project.id)  # snapshot: rollback() expires instances
    root = ws_path(project.id)
    argv = build_argv(type, method, root)
    # R2-03: never spawn the CLI against a settings.yaml that points storage,
    # prompts or the vector store outside this workspace (SettingsValidationError).
    await asyncio.to_thread(check_workspace_settings, project)
    settings = get_settings()
    free = await workspaces_free_bytes()
    if free < settings.disk_watermark_mb * MIB:
        raise DiskWatermarkError(str(free))
    if await usage_bytes(project) > quota_bytes():
        raise ProjectOverQuotaError(settings.project_quota_mb)
    try:
        # Same lock the file/settings/.env mutations take (spec 5.2b): a
        # mutation already holding it makes this wait until its rename has
        # committed, so the start snapshot cannot miss it.
        await lock_project(session, project.id)
        job = await jobs_repo.insert_job(
            session, project_id=project.id, type=type, method=method, argv=argv, queued_by=actor.id
        )
        # Decision D3: every state-changing route audits. Rolled back with
        # the insert when the one-active-job index refuses it.
        await audit(
            session,
            actor.id,
            "job.enqueued",
            "project",
            project_id,
            {"job_id": str(job.id), "type": type, "method": method},
        )
        await session.commit()
    except IntegrityError:
        # jobs_one_active_per_project partial unique index fired: another
        # active job won the race. Never check-then-insert (spec §5).
        await session.rollback()
        raise JobConflictError(project_id) from None
    logger.info(
        "job enqueued id=%s project=%s type=%s method=%s by=%s",
        job.id,
        project_id,
        type,
        method,
        actor.id,
    )
    return job


async def cancel(session: AsyncSession, job: Job, actor: User) -> bool:
    """False when the job is already terminal: nothing changed, nothing is
    audited. request_cancel commits its own write, so the audit row follows
    in a second transaction (decision D3)."""
    job_id, job_type, project_id = job.id, job.type, str(job.project_id)
    if not await jobs_repo.request_cancel(session, job_id):
        return False
    await audit(
        session,
        actor.id,
        "job.cancelled",
        "project",
        project_id,
        {"job_id": str(job_id), "type": job_type},
    )
    await session.commit()
    logger.info("job cancel requested id=%s type=%s by=%s", job_id, job_type, actor.id)
    return True


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await jobs_repo.get_job(session, job_id)


async def list_jobs(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    job_types: Sequence[str] | None = None,
) -> tuple[list[Job], int]:
    return await jobs_repo.list_jobs(
        session, project_id, limit=limit, offset=offset, job_types=job_types
    )


def _empty_dir(path: Path) -> None:
    """Delete everything under path, keep path itself. Sync: runs in a
    to_thread hop."""
    if path.is_dir():
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


async def clear_cache(session: AsyncSession, project: Project, actor: User) -> int:
    """Empty the project's graphrag cache/ (R3-25): the launch warning's
    way out. Refused while any job is active — an index or update writes
    the cache, a test run reads through it. The rows are flushed before
    the irreversible delete, and committed after it."""
    project_id = str(project.id)
    await lock_project(session, project.id)
    if await active_job(session, project.id) is not None:
        await session.rollback()
        raise JobConflictError(project_id)
    cache = ws_path(project.id) / "cache"
    freed = await asyncio.to_thread(tree_bytes, cache)
    await audit(session, actor.id, "cache.cleared", "project", project_id, {"freed_bytes": freed})
    await session.flush()
    try:
        await asyncio.to_thread(_empty_dir, cache)
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    logger.info("cache cleared project=%s freed=%d by=%s", project_id, freed, actor.id)
    return freed


async def preflight(session: AsyncSession, project: Project) -> dict:
    settings = get_settings()
    root = ws_path(project.id)
    last = await jobs_repo.last_succeeded(session, project.id)
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
        "cache_bytes": await asyncio.to_thread(tree_bytes, root / "cache"),
        "cache_quota_mb": settings.cache_quota_mb,
        "usage_bytes": await usage_bytes(project),
        "project_quota_mb": settings.project_quota_mb,
        "disk_free_mb": await workspaces_free_bytes() // MIB,
        "disk_watermark_mb": settings.disk_watermark_mb,
    }
