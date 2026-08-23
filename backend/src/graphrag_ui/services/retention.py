"""Retention policies (spec §6.3): expired job-log deletion, update_output
pruning, and the daily sweep. DB rows are never deleted — history and the
error tail in jobs.error survive; only files are reclaimed."""
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import log_path_for
from graphrag_ui.adapters.models import Job
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from graphrag_ui.services.projects import ws_path

_BATCH = 500


async def sweep_job_logs(session, now: datetime) -> dict:
    """Delete log files of terminal jobs past their retention window.
    succeeded → job_log_retention_days, anything else → the longer
    job_log_failed_retention_days. Commits nothing (file ops only)."""
    settings = get_settings()
    deleted = 0
    offset = 0
    while True:
        # Rows are never deleted by the sweep, so offset paging is stable.
        res = await session.execute(
            select(Job.id, Job.project_id, Job.status, Job.finished_at)
            .where(Job.status.in_(TERMINAL_STATUSES))
            .order_by(Job.queued_at, Job.id)
            .limit(_BATCH).offset(offset))
        rows = res.all()
        for job_id, project_id, status, finished_at in rows:
            if finished_at is None:
                continue  # defensive: finish() always stamps it
            days = (settings.job_log_failed_retention_days
                    if status != "succeeded" else settings.job_log_retention_days)
            if finished_at + timedelta(days=days) >= now:
                continue
            # log_path_for mkdirs parents — skip deleted projects so the
            # sweep never recreates their workspace dirs.
            if not ws_path(project_id).is_dir():
                continue
            log = log_path_for(ws_path(project_id), job_id)
            if log.is_file():
                log.unlink()
                deleted += 1
        if len(rows) < _BATCH:
            break
        offset += _BATCH
    return {"deleted_logs": deleted}


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


async def sweep_all() -> dict:
    """One retention pass over everything: expired job logs (DB-wide) plus
    update_output pruning for every project workspace. Safe when the
    workspaces root is empty or missing."""
    settings = get_settings()
    async with get_session_factory()() as session:
        result = await sweep_job_logs(session, datetime.now(UTC))
        deleted_logs = result["deleted_logs"]
    pruned = 0
    root = Path(settings.workspaces_dir).resolve()
    if root.is_dir():
        for project_dir in _project_dirs(root):
            pruned += prune_update_output(project_dir,
                                          settings.update_output_keep_latest)
    return {"deleted_logs": deleted_logs, "pruned_dirs": pruned}
