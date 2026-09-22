"""Index snapshots: the evidence per-file state is computed from (spec 5.2).

Three parts, and each exists because a simpler version lied:

(a) The start snapshot is captured from input/ BEFORE the CLI spawns. A
    post-hoc scan records the hash of whatever is on disk when the job ends,
    which is not what the indexer read.
(b) The input freeze (services/project_lock.py) keeps input/ still for the
    job's duration, so (a) is actually fixed.
(c) Baseline advancement is type-specific and driven by attributable
    document titles, because graphrag compares documents.title and an
    update does not re-ingest a changed same-name file at all.
"""

import asyncio
import uuid
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path

import yaml
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.artifacts import read_document_titles
from graphrag_ui.adapters.models import IndexSnapshot, IndexSnapshotEntry, Job, Project
from graphrag_ui.domain.artifacts import recover_filenames, title_column_configured
from graphrag_ui.services.files import sha256_file
from graphrag_ui.services.project_lock import FREEZING_JOB_TYPES
from graphrag_ui.services.projects import ws_path


def advance_entries(
    previous: Mapping[str, str], start: Mapping[str, str], pre: AbstractSet[str]
) -> dict[str, str]:
    """The per-name rule for a successful update with recovery available.

    `pre` is the START row's attributable set: it says whether upstream
    already knew this title and therefore skipped it. It is NOT paired with
    a `post` condition - requiring the attempt to have succeeded as well
    would strand a document that was offered and dropped AGAIN (content
    repaired, title still unknown, dropped once more), keeping the stale
    hash so the UI said `modified` when the truth was `skipped`.
    """
    out = dict(previous)  # names absent from `start` are kept: they are the
    # `removed` files, still live in the index
    for name, sha in start.items():
        if name not in previous or name not in pre:
            out[name] = sha
    return out


async def capture_start(
    session: AsyncSession, project_id: uuid.UUID, job_id: uuid.UUID
) -> uuid.UUID:
    """Write the job's `start` snapshot in one transaction of its own;
    returns the snapshot id.

    `candidates` is the previous baseline's entry names UNION the live
    input/ listing - not the live listing alone, because a live listing
    drops exactly the names whose files are gone, and those are the
    documents that stay in the index after a delete.
    """
    prev = await baseline_row(session, project_id)
    prev_names: set[str] = set()
    if prev is not None:
        prev_names = set(await entries_of(session, prev.id))
    entries, titles, title_recovery = await asyncio.to_thread(
        _scan_start_state, ws_path(project_id)
    )
    candidates = frozenset(prev_names | set(entries))
    attributable: list[str] = []
    if title_recovery == "available":
        attributable = sorted(recover_filenames(titles or [], candidates))
    row = IndexSnapshot(
        job_id=job_id,
        project_id=project_id,
        kind="start",
        attributable_titles=attributable,
        title_recovery=title_recovery,
    )
    session.add(row)
    await session.flush()
    session.add_all(
        IndexSnapshotEntry(snapshot_id=row.id, name=name, sha256=sha)
        for name, sha in entries.items()
    )
    await session.commit()
    return row.id


def _scan_start_state(root: Path) -> tuple[dict[str, str], list[str] | None, str]:
    """Disk half of the start snapshot: input/ hashes, indexed titles, and
    the title_column verdict. One to_thread hop — hashing every file in
    input/ is exactly the unbounded-walk shape spec A4 keeps off the loop.

    Every input is optional: workspaces whose job seeded no files (tests,
    freshly created projects) still capture an empty, recovery-tagged row.
    """
    input_dir = root / "input"
    entries: dict[str, str] = {}
    if input_dir.is_dir():
        for p in sorted(input_dir.iterdir()):
            # Dotfiles are upload scratch (.tmp-*) or editor droppings;
            # listings skip them (files._scan_input) and graphrag's own
            # scan does too, so the snapshot must not see them either —
            # a promoted .tmp-* would haunt the union listing as `removed`.
            if p.is_file() and not p.name.startswith("."):
                entries[p.name] = sha256_file(p)
    titles, title_recovery = _scan_titles(root)
    return entries, titles, title_recovery


def _scan_titles(root: Path) -> tuple[list[str] | None, str]:
    """documents.title as it stands on disk right now, plus the recovery
    verdict from the settings.yaml beside it. Both snapshot rows read this:
    the start row before the CLI spawns, the baseline row after it exits
    (spec 5.2c: "evaluated at the moment they are written")."""
    titles = read_document_titles(root)
    settings_path = root / "settings.yaml"
    data = yaml.safe_load(settings_path.read_text()) if settings_path.is_file() else None
    title_recovery = "unavailable_title_column" if title_column_configured(data) else "available"
    return titles, title_recovery


async def bump_artifact_epoch(session: AsyncSession, project_id: uuid.UUID) -> int:
    """Advance projects.artifact_epoch and return the new value, in its own
    transaction: the increment must land even when the attempt later fails,
    because a failed run still rewrote output/ in place."""
    res = await session.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(artifact_epoch=Project.artifact_epoch + 1)
        .returning(Project.artifact_epoch)
        .execution_options(synchronize_session=False)
    )
    epoch = res.scalar_one()
    await session.commit()
    return epoch


async def promote(session: AsyncSession, job: Job) -> None:
    """Promote a terminal job's start snapshot to the project baseline.

    Runs INSIDE the caller's transaction (jobs_repo.finish invokes it before
    its commit) and is a no-op unless the job succeeded and its type
    qualifies. `index` copies the start entries wholesale. `update` learns
    nothing without a previous baseline; with one, a new baseline row is
    ALWAYS written - when title recovery was unavailable at either capture
    the entries carry forward verbatim, otherwise advance_entries applies.

    The baseline's attributable set is recovered from the documents.parquet
    the run PRODUCED, never copied from the start row: the start row was
    captured before the CLI spawned, against the previous output (none at
    all on a first index), so copying it marked every file the run had just
    ingested `skipped` (R1-67 / R3-01). The candidates are the new baseline's
    own entry names — for an update that is previous ∪ start, so a document
    deleted from input/ but still in the merged index stays attributable.
    """
    if job.status != "succeeded" or job.type not in FREEZING_JOB_TYPES:
        return
    start = await _start_row(session, job.id)
    if start is None:
        return
    entries: Mapping[str, str] = {}
    if job.type == "update":
        prev = await baseline_row(session, job.project_id)
        if prev is None:
            return
        prev_entries = await entries_of(session, prev.id)
        if prev.title_recovery == "available" and start.title_recovery == "available":
            entries = advance_entries(
                prev_entries, await entries_of(session, start.id), set(start.attributable_titles)
            )
        else:
            entries = prev_entries
    else:
        entries = await entries_of(session, start.id)
    titles, title_recovery = await asyncio.to_thread(_scan_titles, ws_path(job.project_id))
    attributable: list[str] = []
    if title_recovery == "available":
        attributable = sorted(recover_filenames(titles or [], frozenset(entries)))
    # Scalar SELECT, not the identity map: bump_artifact_epoch may have run
    # through this same session, leaving a stale instance behind.
    epoch = (
        await session.execute(select(Project.artifact_epoch).where(Project.id == job.project_id))
    ).scalar_one()
    row = IndexSnapshot(
        job_id=job.id,
        project_id=job.project_id,
        kind="baseline",
        attributable_titles=attributable,
        title_recovery=title_recovery,
        artifact_epoch=epoch,
    )
    session.add(row)
    await session.flush()
    session.add_all(
        IndexSnapshotEntry(snapshot_id=row.id, name=name, sha256=sha)
        for name, sha in entries.items()
    )
    await session.execute(
        update(Project)
        .where(Project.id == job.project_id)
        .values(baseline_snapshot_id=row.id)
        .execution_options(synchronize_session=False)
    )


async def promote_after_finish(session: AsyncSession, job_id: uuid.UUID, status: str) -> None:
    """The on_before_commit callback shape for jobs_repo.finish: re-read the
    job row inside the finishing transaction — the runner's Job instance
    belongs to a session that is already closed — and delegate to promote().
    The terminal UPDATE has run on this connection, so the re-read carries
    the new status; `status` guards against anything else slipping through.
    """
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is not None and job.status == status:
        await promote(session, job)


async def baseline_row(session: AsyncSession, project_id: uuid.UUID) -> IndexSnapshot | None:
    """The snapshot row projects.baseline_snapshot_id points at, or None."""
    return (
        await session.execute(
            select(IndexSnapshot)
            .join(Project, Project.baseline_snapshot_id == IndexSnapshot.id)
            .where(Project.id == project_id)
        )
    ).scalar_one_or_none()


async def baseline_entries(session: AsyncSession, project_id: uuid.UUID) -> dict[str, str]:
    row = await baseline_row(session, project_id)
    return {} if row is None else await entries_of(session, row.id)


async def entries_of(session: AsyncSession, snapshot_id: uuid.UUID) -> dict[str, str]:
    res = await session.execute(
        select(IndexSnapshotEntry.name, IndexSnapshotEntry.sha256)
        .where(IndexSnapshotEntry.snapshot_id == snapshot_id)
        .order_by(IndexSnapshotEntry.name)
    )
    return {name: sha for name, sha in res.all()}


async def kinds_of(session: AsyncSession, job_id: uuid.UUID) -> set[str]:
    res = await session.execute(select(IndexSnapshot.kind).where(IndexSnapshot.job_id == job_id))
    return set(res.scalars().all())


async def _start_row(session: AsyncSession, job_id: uuid.UUID) -> IndexSnapshot | None:
    return (
        await session.execute(
            select(IndexSnapshot).where(
                IndexSnapshot.job_id == job_id, IndexSnapshot.kind == "start"
            )
        )
    ).scalar_one_or_none()
