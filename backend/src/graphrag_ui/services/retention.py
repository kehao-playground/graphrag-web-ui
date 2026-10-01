"""Retention policies (spec §6.3): expired job-log deletion, update_output
pruning, superseded index-snapshot pruning, and the daily sweep. Job rows
are never deleted — history and the error tail in jobs.error survive — and
neither is any `baseline` snapshot row; what is reclaimed are files and the
`start` snapshot rows of terminal jobs past their log window, which are
superseded input hashes, not history."""

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import log_path_for
from graphrag_ui.adapters.models import IndexSnapshot, Job, Project
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from graphrag_ui.services.projects import ws_path

logger = logging.getLogger(__name__)


def _log_windows() -> tuple[tuple[tuple[str, ...], int], ...]:
    """(terminal statuses, retention days): succeeded logs keep
    job_log_retention_days, every other terminal status the longer
    job_log_failed_retention_days. Logs and start snapshots share it."""
    settings = get_settings()
    return (
        (("succeeded",), settings.job_log_retention_days),
        (tuple(TERMINAL_STATUSES - {"succeeded"}), settings.job_log_failed_retention_days),
    )


def _unlink_logs(jobs: list[tuple[uuid.UUID, uuid.UUID]]) -> int:
    """Blocking file half of sweep_job_logs, run in a worker thread. A
    missing log (or workspace) is simply not counted."""
    deleted = 0
    for job_id, project_id in jobs:
        try:
            log_path_for(ws_path(project_id), job_id).unlink()
        except FileNotFoundError:
            continue
        deleted += 1
    return deleted


async def sweep_job_logs(session, now: datetime) -> dict:
    """Delete log files of terminal jobs past their retention window
    (_log_windows). The window is filtered in SQL, one query per status
    group, so jobs inside it never leave the database. Commits nothing
    (file ops only)."""
    expired: list[tuple[uuid.UUID, uuid.UUID]] = []
    for statuses, days in _log_windows():
        res = await session.execute(
            select(Job.id, Job.project_id).where(
                Job.status.in_(statuses),
                # keep while finished_at + window >= now
                Job.finished_at + timedelta(days=days) < now,
            )
        )
        expired.extend((job_id, project_id) for job_id, project_id in res.all())
    if not expired:
        return {"deleted_logs": 0}
    return {"deleted_logs": await asyncio.to_thread(_unlink_logs, expired)}


async def sweep_index_snapshots(session, now: datetime) -> dict:
    """Delete `start` snapshot rows of terminal jobs past the same retention
    window their logs use. NEVER touches the row referenced by
    projects.baseline_snapshot_id, nor the start row of the job that produced
    it - those are the current evidence, not superseded input hashes.
    `baseline` rows are never pruned here."""
    # The job ids behind every current baseline pointer: their start rows
    # stay even once past the window (current evidence beats retention).
    protected_jobs = select(IndexSnapshot.job_id).where(
        IndexSnapshot.id.in_(
            select(Project.baseline_snapshot_id).where(Project.baseline_snapshot_id.is_not(None))
        )
    )
    deleted = 0
    for statuses, days in _log_windows():
        res = await session.execute(
            select(IndexSnapshot.id)
            .join(Job, Job.id == IndexSnapshot.job_id)
            .where(
                IndexSnapshot.kind == "start",
                IndexSnapshot.job_id.not_in(protected_jobs),
                Job.status.in_(statuses),
                # same cutoff semantics as sweep_job_logs: keep while
                # finished_at + window >= now
                Job.finished_at + timedelta(days=days) < now,
            )
        )
        ids = list(res.scalars().all())
        if ids:
            await session.execute(
                delete(IndexSnapshot)
                .where(IndexSnapshot.id.in_(ids))
                .execution_options(synchronize_session=False)
            )
        deleted += len(ids)
    await session.commit()
    return {"deleted_snapshots": deleted}


def prune_update_output(root: Path, keep_latest: int) -> int:
    """Keep the newest `keep_latest` timestamp dirs under update_output/,
    rmtree the rest. Timestamp dir names sort lexically (spec stats path)."""
    base = root / "update_output"
    if not base.is_dir():
        return 0
    dirs = sorted(d for d in base.iterdir() if d.is_dir())
    victims = dirs[:-keep_latest] if keep_latest > 0 else dirs
    for d in victims:
        shutil.rmtree(d, ignore_errors=True)
    return len(victims)


def _project_dirs(root: Path) -> list[Path]:
    """Subdirectories of the workspaces root named by a project UUID."""
    out = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        try:
            uuid.UUID(d.name)
        except ValueError:
            continue
        out.append(d)
    return out


def _prune_all(root: Path, keep_latest: int) -> int:
    """prune_update_output for every project workspace; blocking (walks and
    rmtrees), so sweep_all runs it in a worker thread (R2-19)."""
    if not root.is_dir():
        return 0
    return sum(prune_update_output(d, keep_latest) for d in _project_dirs(root))


async def sweep_all() -> dict:
    """One retention pass over everything: expired job logs (DB-wide),
    superseded start snapshots, and update_output pruning for every project
    workspace. Safe when the workspaces root is empty or missing."""
    settings = get_settings()
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        result = await sweep_job_logs(session, now)
        deleted_logs = result["deleted_logs"]
        result = await sweep_index_snapshots(session, now)
        deleted_snapshots = result["deleted_snapshots"]
    pruned = await asyncio.to_thread(
        _prune_all, Path(settings.workspaces_dir).resolve(), settings.update_output_keep_latest
    )
    logger.info(
        "retention sweep: deleted %d job logs, %d start snapshots; pruned %d update_output dirs",
        deleted_logs,
        deleted_snapshots,
        pruned,
    )
    return {
        "deleted_logs": deleted_logs,
        "pruned_dirs": pruned,
        "deleted_snapshots": deleted_snapshots,
    }
