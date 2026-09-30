"""The Documents listing: input/ UNION the baseline's filenames with each
document's index state, and the project_files write-back the scan feeds
(spec 6.3, 7.5)."""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import FileTag, FileTagLink, Project, ProjectFile
from graphrag_ui.domain.files import AttributableTitles, IngestCheck, index_state
from graphrag_ui.services.index_snapshots import baseline_rows, entries_by_snapshot
from graphrag_ui.services.input_scan import ScannedFile, cached_scans, scan_input
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.projects import ws_path


def discovered_row(
    project_id: uuid.UUID, name: str, sha256: str, size: int, *, mtime_ns: int | None = None
) -> ProjectFile:
    """The row for a file found in input/ without one (copied in, not
    uploaded): no uploader, stamped discovered now."""
    return ProjectFile(
        project_id=project_id,
        name=name,
        sha256=sha256,
        size=size,
        mtime_ns=mtime_ns,
        discovered_at=datetime.now(UTC),
    )


async def list_files(session: AsyncSession, project: Project) -> dict:
    """The Documents listing: file_listings for one project plus each
    on-disk file's tags (a `removed` document has no row and no tags)."""
    listing = (await file_listings(session, [project]))[project.id]
    tags: dict[str, list[str]] = {}
    for file_name, tag_name in (
        await session.execute(
            select(ProjectFile.name, FileTag.name)
            .join(FileTagLink, FileTagLink.file_id == ProjectFile.id)
            .join(FileTag, FileTag.id == FileTagLink.tag_id)
            .where(ProjectFile.project_id == project.id)
            .order_by(FileTag.name)
        )
    ).all():
        tags.setdefault(file_name, []).append(tag_name)
    for entry in listing["files"]:
        entry["tags"] = tags.get(entry["name"], []) if entry["size"] is not None else []
    return listing


async def file_listings(
    session: AsyncSession, projects: Sequence[Project]
) -> dict[uuid.UUID, dict]:
    """Per project: rows are input/ UNION the baseline's filenames, sorted
    by name. One query per table for the whole batch (R1-71), so the
    project list's batch health costs the same round trips for 1 or 200
    projects.

    Reads the BASELINE SNAPSHOT ROW, never documents.parquet: recovery
    provenance was evaluated when the artifacts were produced, and
    re-deriving it here would bind the answer to today's configuration
    (spec 6.3). The only artifact touch is a stat on
    output/documents.parquet, which distinguishes unavailable_not_indexed
    from available.

    Returns {pid: {"files": [...], "ingest_check": str, "has_baseline":
    bool, "artifacts_stale": bool}}. A name with no file behind it (a
    `removed` document) carries NULL size/modified_at/sha256 rather than
    invented values. artifacts_stale is only meaningful with a baseline: a
    failed FIRST index bumps the epoch while promoting nothing, which is
    rule 3's overview case, not artifact wreckage (spec 7.5).
    """
    if not projects:
        return {}
    ids = [p.id for p in projects]
    rows = await _file_rows(session, ids)
    scans = await asyncio.to_thread(
        lambda: {
            p.id: scan_input(ws_path(p.id) / "input", cached_scans(rows.get(p.id, {})))
            for p in projects
        }
    )
    await _sync_file_rows(session, scans, rows)
    baselines = await baseline_rows(session, ids)
    entries = await entries_by_snapshot(session, [b.id for b in baselines.values()])

    out: dict[uuid.UUID, dict] = {}
    for project in projects:
        scanned = scans[project.id]
        base = baselines.get(project.id)
        baseline = entries.get(base.id, {}) if base is not None else {}
        if base is None:
            check = IngestCheck.unavailable_no_baseline
        elif base.title_recovery == "unavailable_title_column":
            check = IngestCheck.unavailable_title_column
        elif not _documents_parquet_exists(ws_path(project.id)):
            check = IngestCheck.unavailable_not_indexed
        else:
            check = IngestCheck.available
        attributable = (
            AttributableTitles.of(base.attributable_titles)
            if base is not None and check == IngestCheck.available
            else AttributableTitles.unavailable()
        )
        files = []
        for name in sorted(set(scanned) | set(baseline)):
            on_disk = scanned.get(name)
            sha = on_disk.sha256 if on_disk else None
            files.append(
                {
                    "name": name,
                    "size": on_disk.size if on_disk else None,
                    "modified_at": on_disk.modified_at if on_disk else None,
                    "sha256": sha,
                    "index_state": index_state(name, sha, baseline, attributable).value,
                }
            )
        out[project.id] = {
            "files": files,
            "ingest_check": check.value,
            "has_baseline": base is not None,
            "artifacts_stale": base is not None and base.artifact_epoch != project.artifact_epoch,
        }
    return out


async def _file_rows(
    session: AsyncSession, project_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, ProjectFile]]:
    by_project: dict[uuid.UUID, dict[str, ProjectFile]] = {}
    for row in (
        await session.execute(select(ProjectFile).where(ProjectFile.project_id.in_(project_ids)))
    ).scalars():
        by_project.setdefault(row.project_id, {})[row.name] = row
    return by_project


async def _sync_file_rows(
    session: AsyncSession,
    scans: Mapping[uuid.UUID, Mapping[str, ScannedFile]],
    rows: Mapping[uuid.UUID, Mapping[str, ProjectFile]],
) -> None:
    """Write back what the scans learned, and commit only when they learned
    something (R1-70): a listing over unchanged, tracked files writes
    nothing and takes no lock.

    Refreshed hashes are one executemany of plain UPDATEs by id, lock-free:
    a racing writer (upload, delete) at worst leaves a row describing an
    older state of the file, whose mtime no longer matches, so the next
    scan re-hashes. Discovery (files that predate this release have no row,
    and nothing on disk records who uploaded them: uploaded_by/uploaded_at
    NULL, discovered_at set) re-checks the names under the project-row
    lock, so concurrent listings cannot double-insert —
    uq_project_files_project_name backs the check.
    """
    refreshed: list[dict] = []
    missing: dict[uuid.UUID, list[str]] = {}
    for pid, scanned in scans.items():
        known = rows.get(pid, {})
        for name, f in scanned.items():
            row = known.get(name)
            if row is None:
                missing.setdefault(pid, []).append(name)
            elif (row.size, row.mtime_ns, row.sha256) != (f.size, f.cache_mtime_ns, f.sha256):
                refreshed.append(
                    {
                        "b_id": row.id,
                        "b_sha": f.sha256,
                        "b_size": f.size,
                        "b_mtime": f.cache_mtime_ns,
                    }
                )
    if refreshed:
        # Core UPDATE on the table: an ORM executemany would demand every
        # id still exist, and a row a concurrent delete removed is fine.
        table = cast(Table, ProjectFile.__table__)
        await session.execute(
            update(table)
            .where(table.c.id == bindparam("b_id"))
            .values(
                sha256=bindparam("b_sha"), size=bindparam("b_size"), mtime_ns=bindparam("b_mtime")
            ),
            refreshed,
        )
    for pid, names in missing.items():
        await lock_project(session, pid)
        tracked = set(
            (
                await session.execute(
                    select(ProjectFile.name).where(
                        ProjectFile.project_id == pid, ProjectFile.name.in_(names)
                    )
                )
            ).scalars()
        )
        session.add_all(
            discovered_row(
                pid,
                name,
                scans[pid][name].sha256,
                scans[pid][name].size,
                mtime_ns=scans[pid][name].cache_mtime_ns,
            )
            for name in names
            if name not in tracked
        )
    if refreshed or missing:
        await session.commit()


def _documents_parquet_exists(root: Path) -> bool:
    """A cheap stat, not a read: proves output/documents.parquet exists and
    nothing more (a present-but-corrupt parquet is outside what the ingest
    check detects)."""
    try:
        (root / "output" / "documents.parquet").stat()
    except OSError:
        return False
    return True
