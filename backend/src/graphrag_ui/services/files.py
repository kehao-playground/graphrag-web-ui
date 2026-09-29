"""Project input-file storage: whitelist, size/quota limits, path-safe writes.

Spec §6.5 (per-project format whitelist) and §10 (path traversal protection,
upload cap, per-project quota over input/ + output/). Services never touch
HTTP — error classes are translated to status codes by the api layer.
"""

import asyncio
import functools
import hashlib
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, cast

from sqlalchemy import Table, bindparam, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import (
    FileTag,
    FileTagLink,
    Project,
    ProjectFile,
    TestResult,
    TestRun,
)
from graphrag_ui.config import get_settings
from graphrag_ui.domain.files import AttributableTitles, IngestCheck, index_state
from graphrag_ui.services.audit import audit
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.project_lock import input_mutation, lock_project
from graphrag_ui.services.projects import ws_path

# Upload whitelist keyed by project.input_file_type (spec §6.5):
# text → txt/md, csv → csv, json → json.
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "text": {".txt", ".md"},
    "csv": {".csv"},
    "json": {".json"},
}


class FileServiceError(CodedServiceError):
    """Invalid filename/extension — maps to 400 (code/params let the client
    localize)."""

    def __init__(self, code: str, detail: str, params: dict[str, str] | None = None) -> None:
        super().__init__(detail, code=code, params=params)


class FileTooLargeError(CodedServiceError):
    """Single file above upload_max_file_mb — maps to 413."""

    code = "file_too_large"

    def __init__(self, max_mb: int) -> None:
        super().__init__(f"file exceeds the {max_mb} MiB upload limit", params={"max_mb": max_mb})


class QuotaExceededError(CodedServiceError):
    """input/+output/ usage above project_quota_mb — maps to 413."""

    code = "quota_exceeded"

    def __init__(self, quota_mb: int) -> None:
        super().__init__(
            f"project storage quota of {quota_mb} MiB exceeded", params={"quota_mb": quota_mb}
        )


class InputFileNotFoundError(CodedServiceError, LookupError):
    """No file of that name under input/ (a `removed` row has none either)
    — maps to 404. A service contract, not an OSError: a stray
    FileNotFoundError from anywhere else stays a 500 (R1-27)."""

    code = "file_not_found"


class LocatorMismatchError(CodedServiceError):
    """A {result_id, entry_id} locator failed one of its three bindings
    (spec 7.4). Carries no detail on purpose: unknown and mismatched must
    stay indistinguishable, so every failure maps to one fixed 404 — never
    a 403, which would confirm the row exists."""

    code = "citation_not_found"


_MIB = 1024 * 1024
_CHUNK_BYTES = _MIB  # streaming read granularity for uploads (bounded memory)


def _safe_name(project_input_file_type: str, filename: str) -> str:
    """Validate a client-supplied filename; returns the name unchanged.

    Every rejection happens before the name ever reaches the filesystem, so
    no path variant (separator, '..', leading dot) can escape input/.
    """
    if not filename:
        raise FileServiceError("file_name_empty", "filename must not be empty")
    if len(filename) > 255:
        raise FileServiceError("file_name_too_long", "filename exceeds 255 characters")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise FileServiceError(
            "file_name_unsafe", "filename must not contain path separators or '..'"
        )
    if filename.startswith("."):
        raise FileServiceError("file_name_leading_dot", "filename must not start with '.'")
    allowed = ALLOWED_EXTENSIONS.get(project_input_file_type, set())
    # Compare lowercased: Windows clients commonly send .MD / .Txt. The
    # stored name keeps its original case (only the match is case-blind).
    ext = Path(filename).suffix.lower()
    if ext not in allowed:
        raise FileServiceError(
            "file_ext_not_allowed",
            f"extension '{ext or '(none)'}' not allowed for "
            f"input_file_type '{project_input_file_type}'",
            {"ext": ext or "(none)", "input_file_type": project_input_file_type},
        )
    return filename


# save_file streams into input/ under this prefix; the dot keeps listings
# from surfacing it and the quota from counting bytes not yet stored.
_UPLOAD_TMP_PREFIX = ".tmp-"


def _dir_size(path: Path) -> int:
    """Recursive byte size; 0 when the directory does not exist yet.
    In-flight upload tmp files are skipped: they are not stored yet, and
    counting a concurrent upload's partial bytes would refuse uploads that
    fit."""
    if not path.exists():
        return 0
    return sum(
        p.stat().st_size
        for p in path.rglob("*")
        if p.is_file() and not p.name.startswith(_UPLOAD_TMP_PREFIX)
    )


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file nobody just uploaded (scan_input on a
    cache miss, inline discovery in add_tags)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


# Preview contract limits (spec 7.4): module-level constants, not env vars —
# the frontend sizes its drawer around the window and the API around the
# passage bound.
PREVIEW_WINDOW_BYTES = 64 * 1024
PASSAGE_MAX_BYTES = 4096


def _preview_core(path: Path, needle: bytes | None) -> dict:
    """One bounded window of the file, centered on the first occurrence of
    `needle` anywhere in it when given, else the head.

    The window cap bounds the RESPONSE, never the scan: with a passage the
    file is streamed in _CHUNK_BYTES blocks carrying an overlap of
    len(needle) - 1 bytes, so a passage spanning a chunk boundary is still
    found (spec 7.4).
    """
    total = path.stat().st_size
    with path.open("rb") as fh:
        if needle is None:
            return {
                "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
                "offset": 0,
                "total_size": total,
                "match": False,
            }
        overlap = len(needle) - 1
        base = 0  # absolute offset of buf[0]
        carry = b""
        found = -1
        while True:
            chunk = fh.read(_CHUNK_BYTES)
            if not chunk:
                break
            buf = carry + chunk
            idx = buf.find(needle)
            if idx != -1:
                found = base + idx
                break
            carry = buf[-overlap:] if overlap > 0 else b""
            base += len(buf) - len(carry)
        if found == -1:
            # Unmatched: the head plus match=False, rather than pretending.
            fh.seek(0)
            return {
                "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
                "offset": 0,
                "total_size": total,
                "match": False,
            }
        start = max(0, found - PREVIEW_WINDOW_BYTES // 2)
        fh.seek(start)
        return {
            "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
            "offset": start,
            "total_size": total,
            "match": True,
        }


async def preview_file(project: Project, name: str, *, around: str | None = None) -> dict:
    """{"text", "offset", "total_size", "match"} for input/<name>; the
    bounded scan runs off the event loop (spec A4)."""
    name = _safe_name(project.input_file_type, name)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        # A `removed` row has no file behind it; there is nothing to preview.
        raise InputFileNotFoundError(name)
    needle = around.encode("utf-8") if around is not None else None
    return await asyncio.to_thread(_preview_core, target, needle)


async def resolve_stored_passage(
    session: AsyncSession,
    project_id: uuid.UUID,
    result_id: uuid.UUID,
    entry_id: int,
    name: str,
) -> str:
    """The stored passage a historic locator points at (spec 7.4).

    Three bindings, any mismatch -> LocatorMismatchError: the result's run must
    belong to `project_id`, `entry_id` must name an entry of a Sources
    citation on that result, and that entry's stored source_name must equal
    `name` (the confused-deputy stop: without it, a real result_id paired
    with any filename would search a different document). One exception for
    all three so the caller cannot learn which binding failed.
    """
    citations = (
        await session.execute(
            select(TestResult.citations)
            .join(TestRun, TestRun.id == TestResult.run_id)
            .where(TestResult.id == result_id, TestRun.project_id == project_id)
        )
    ).scalar_one_or_none()
    if citations is not None:
        for citation in citations:
            if citation.get("label") != "Sources":
                continue
            for entry in citation.get("entries") or []:
                if entry.get("id") == entry_id:
                    # A matched entry without a usable stored passage (no
                    # source_name, no text) is as unopenable as no match.
                    if entry.get("source_name") == name and entry.get("text"):
                        return str(entry["text"])
                    raise LocatorMismatchError
    raise LocatorMismatchError


def quota_bytes() -> int:
    return get_settings().project_quota_mb * _MIB


def max_file_bytes() -> int:
    return get_settings().upload_max_file_mb * _MIB


def _usage_bytes_sync(project: Project) -> int:
    """input/ + output/ both count against the project quota (spec §10)."""
    root = ws_path(project.id)
    return _dir_size(root / "input") + _dir_size(root / "output")


async def usage_bytes(project: Project) -> int:
    # Two rglob walks over input/+output/ are unbounded (spec A4) — one
    # to_thread hop for the whole computation, never on the event loop.
    return await asyncio.to_thread(_usage_bytes_sync, project)


def _replace_into_input(tmp: Path, target: Path) -> None:
    """Atomic rename; a seam so tests can park inside the locked transaction."""
    os.replace(tmp, target)


async def _upsert_project_file(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str,
    sha256: str,
    size: int,
    actor_id: uuid.UUID | None,
) -> None:
    """Insert or refresh the project_files row for an uploaded name: the
    upload is the source of the row (Task 1's model — a row per file in
    input/), so an upload resets discovered_at to None."""
    row = (
        await session.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id, ProjectFile.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProjectFile(project_id=project_id, name=name)
        session.add(row)
    row.sha256 = sha256
    row.size = size
    # The upload's own hash, but not cached: the renamed file's mtime is
    # inside the racy window (scan_input), so the next listing re-hashes it
    # once and caches that.
    row.mtime_ns = None
    row.uploaded_by = actor_id
    row.uploaded_at = datetime.now(UTC)
    row.discovered_at = None


async def _commit_upload(
    session: AsyncSession,
    project: Project,
    name: str,
    size: int,
    sha: str,
    actor_id: uuid.UUID | None,
    tmp: Path,
    target: Path,
) -> None:
    """The committing transaction (input_mutation): lock, freeze re-check,
    quota check, audit row and project_files, flush, rename, commit.

    The lock is taken HERE and not around the upload stream: holding it for
    the whole of a multi-gigabyte upload would block job enqueue for that
    long. Taking it only for the rename keeps the window short and still
    serializes, because enqueue takes the same lock (spec 5.2b).

    The quota is measured inside the lock (R2-28): measured before it, two
    uploads that each fit alone would both pass. The walk is bounded by the
    project's own input/+output/; an existing file of the same name still
    counts (the overwrite is judged conservatively).
    """
    async with input_mutation(session, project.id) as m:
        if await usage_bytes(project) + size > quota_bytes():
            raise QuotaExceededError(get_settings().project_quota_mb)
        await audit(
            session,
            actor_id,
            "file.uploaded",
            "project",
            str(project.id),
            {"name": name, "size": size},
        )
        await _upsert_project_file(
            session, project.id, name=name, sha256=sha, size=size, actor_id=actor_id
        )
        await m.apply(lambda: _replace_into_input(tmp, target))


async def save_file(
    session: AsyncSession, project: Project, filename: str, source, actor_id: uuid.UUID | None
) -> tuple[str, int]:
    """Stream `source` (any reader with `async read(n)`, e.g. UploadFile) to
    input/<name> in fixed chunks; returns (stored name, byte size).

    The upload is never materialized in memory: the single-file cap is
    enforced while streaming (abort as soon as the running total passes
    max_file_bytes), so an arbitrarily large request body costs at most one
    chunk of RAM (spec §8.2 — uploads share the pod's memory budget with the
    indexer). Overwriting an existing name is allowed (idempotent re-upload).

    Also owns the audit row + transaction (spec A1): the committing half
    lives in _commit_upload, which takes the project-row lock, re-checks
    the input freeze and the quota, flushes the file.uploaded +
    project_files rows and only then renames, so any failure (stream, cap,
    quota, freeze, rename) rolls the rows back and leaves no partial file
    behind.
    """
    name = _safe_name(project.input_file_type, filename)
    input_dir = ws_path(project.id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    # tmp+replace keeps writes atomic: readers never see a partial file. The
    # tmp name is dot-prefixed so a concurrent listing never surfaces it.
    tmp = input_dir / f"{_UPLOAD_TMP_PREFIX}{uuid.uuid4().hex}"
    size = 0
    h = hashlib.sha256()
    try:
        with tmp.open("wb") as out:
            while chunk := await source.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > max_file_bytes():
                    raise FileTooLargeError(get_settings().upload_max_file_mb)
                h.update(chunk)
                out.write(chunk)
        # The quota needs the final size, so _commit_upload checks it once
        # the stream is consumed, inside the lock and before any row.
        await _commit_upload(
            session, project, name, size, h.hexdigest(), actor_id, tmp, input_dir / name
        )
        return name, size
    finally:
        tmp.unlink(missing_ok=True)  # no-op after a successful replace


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
    # Deferred: index_snapshots imports scan_input from this module, so a
    # top-level import here would be circular.
    from graphrag_ui.services.index_snapshots import baseline_rows, entries_by_snapshot

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


class ScannedFile(NamedTuple):
    """One input/ file as a listing sees it. cache_mtime_ns is the
    st_mtime_ns the hash may be cached under, or None while the file is
    inside the racy window (see scan_input)."""

    size: int
    mtime_ns: int
    sha256: str
    cache_mtime_ns: int | None

    @property
    def modified_at(self) -> str:
        return datetime.fromtimestamp(self.mtime_ns / 1e9, tz=UTC).isoformat()


# Filesystem timestamps are coarse (a kernel tick; seconds on some
# filesystems): a same-size rewrite inside one tick keeps (size, mtime).
# A hash is only cached once the file's mtime is older than this window at
# the moment the scan starts, so any later write necessarily moves mtime —
# git's "racy clean" rule.
_RACY_WINDOW_NS = 2_000_000_000


def cached_scans(rows: Mapping[str, ProjectFile]) -> dict[str, tuple[int, int, str]]:
    """{name: (size, mtime_ns, sha256)} for rows whose hash is cached."""
    return {
        name: (row.size, row.mtime_ns, row.sha256)
        for name, row in rows.items()
        if row.mtime_ns is not None
    }


def scan_input(
    input_dir: Path, cache: Mapping[str, tuple[int, int, str]] | None = None
) -> dict[str, ScannedFile]:
    """iterdir/stat/sha256 over input/ — the ONE definition of what counts
    as an input file, shared by listings and the start snapshot (R1-96).

    A file whose (size, st_mtime_ns) match its `cache` entry is not read
    again (R1-69); every other file is hashed, which is unbounded (spec A4),
    so callers run this off the event loop. The stat precedes the hash: a
    write racing the hash leaves a stored mtime older than the file's, so
    the next scan hashes again. Dotfiles are skipped: they are never valid
    uploads, and the only writer here (save_file) uses dot-prefixed tmp
    names during atomic writes; graphrag's own scan skips them too.
    """
    if not input_dir.is_dir():
        return {}
    cache = cache or {}
    settled_before = time.time_ns() - _RACY_WINDOW_NS
    entries: dict[str, ScannedFile] = {}
    for p in sorted(input_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()
        hit = cache.get(p.name)
        if hit is not None and hit[:2] == (st.st_size, st.st_mtime_ns):
            sha = hit[2]
        else:
            sha = sha256_file(p)
        entries[p.name] = ScannedFile(
            st.st_size,
            st.st_mtime_ns,
            sha,
            st.st_mtime_ns if st.st_mtime_ns < settled_before else None,
        )
    return entries


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
        now = datetime.now(UTC)
        session.add_all(
            ProjectFile(
                project_id=pid,
                name=name,
                sha256=scans[pid][name].sha256,
                size=scans[pid][name].size,
                mtime_ns=scans[pid][name].cache_mtime_ns,
                discovered_at=now,
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


async def delete_file(
    session: AsyncSession, project: Project, filename: str, actor_id: uuid.UUID | None
) -> int:
    """Remove input/<name> AND audit it, one transaction (spec A1); returns
    the removed file's size. The project_files row goes with the file.

    InputFileNotFoundError is raised before any audit row; ProjectIndexingError
    (spec 5.2b) is raised inside the project lock, before any row or unlink.
    Residual (spec A1, accepted): a commit failure after the unlink loses
    the audit row.
    """
    name = _safe_name(project.input_file_type, filename)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        raise InputFileNotFoundError(name)
    size = target.stat().st_size
    async with input_mutation(session, project.id) as m:
        await audit(
            session,
            actor_id,
            "file.deleted",
            "project",
            str(project.id),
            {"name": name, "size": size},
        )
        # file_tag_links cascade at the FK level (Task 1's model)
        await session.execute(
            delete(ProjectFile).where(
                ProjectFile.project_id == project.id, ProjectFile.name == name
            )
        )
        await m.apply(target.unlink)
    return size


async def add_tags(
    session: AsyncSession, project: Project, name: str, tags: list[str], actor_id: uuid.UUID | None
) -> None:
    """Attach tags to input/<name> and audit file.tagged, one transaction.

    Tags are metadata, NOT input (spec 8): no freeze check — tagging while
    an index runs changes nothing the indexer reads. The project lock is
    still taken, because the write touches project_files-adjacent rows and
    discovery may run concurrently.
    """
    name = _safe_name(project.input_file_type, name)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        raise InputFileNotFoundError(name)
    unique_tags = sorted(dict.fromkeys(tags))
    by_name = select(ProjectFile).where(
        ProjectFile.project_id == project.id, ProjectFile.name == name
    )
    # A file nobody listed yet has no project_files row; tags attach to the
    # row, so discover it inline (the same shape a listing's discovery
    # produces). Only then is the file hashed — the unbounded read (spec A4)
    # runs off the lock and off the loop; a tracked file's row already
    # carries its hash (R1-69).
    sha: str | None = None
    if (await session.execute(by_name)).scalar_one_or_none() is None:
        sha = await asyncio.to_thread(sha256_file, target)
    async with input_mutation(session, project.id, freeze=False):
        row = (await session.execute(by_name)).scalar_one_or_none()
        if row is None:
            if sha is None:  # its row vanished since the check: hash now
                sha = await asyncio.to_thread(sha256_file, target)
            row = ProjectFile(
                project_id=project.id,
                name=name,
                sha256=sha,
                size=target.stat().st_size,
                discovered_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
        have = set(
            (
                await session.execute(
                    select(FileTag.name).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                )
            ).scalars()
        )
        session.add_all(
            FileTag(project_id=project.id, name=t) for t in unique_tags if t not in have
        )
        await session.flush()
        tag_ids = set(
            (
                await session.execute(
                    select(FileTag.id).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                )
            ).scalars()
        )
        linked = set(
            (
                await session.execute(
                    select(FileTagLink.tag_id).where(FileTagLink.file_id == row.id)
                )
            ).scalars()
        )
        session.add_all(FileTagLink(file_id=row.id, tag_id=tid) for tid in tag_ids - linked)
        await audit(
            session,
            actor_id,
            "file.tagged",
            "project",
            str(project.id),
            {"name": name, "tags": unique_tags},
        )


async def remove_tags(
    session: AsyncSession, project: Project, name: str, tags: list[str], actor_id: uuid.UUID | None
) -> None:
    """Detach tags from input/<name> and audit file.untagged, one
    transaction. Same boundary as add_tags: no freeze, project lock yes."""
    name = _safe_name(project.input_file_type, name)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        raise InputFileNotFoundError(name)
    unique_tags = sorted(dict.fromkeys(tags))
    async with input_mutation(session, project.id, freeze=False):
        # Deleting through subselects keeps it one statement shaped the same
        # regardless of how many tags are detached.
        await session.execute(
            delete(FileTagLink).where(
                FileTagLink.file_id.in_(
                    select(ProjectFile.id).where(
                        ProjectFile.project_id == project.id, ProjectFile.name == name
                    )
                ),
                FileTagLink.tag_id.in_(
                    select(FileTag.id).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                ),
            )
        )
        await audit(
            session,
            actor_id,
            "file.untagged",
            "project",
            str(project.id),
            {"name": name, "tags": unique_tags},
        )


async def list_tags(session: AsyncSession, project: Project) -> list[dict]:
    """The project's tag catalog with live link counts — one grouped query.
    A tag with zero links still lists (count 0): it stays the project's
    vocabulary, and suggesting it in a picker costs nothing."""
    rows = (
        await session.execute(
            select(FileTag.name, func.count(FileTagLink.tag_id))
            .outerjoin(FileTagLink, FileTagLink.tag_id == FileTag.id)
            .where(FileTag.project_id == project.id)
            .group_by(FileTag.name)
            .order_by(FileTag.name)
        )
    ).all()
    return [{"name": name, "count": int(count)} for name, count in rows]


async def bulk_delete(
    session: AsyncSession, project: Project, names: list[str], actor_id: uuid.UUID | None
) -> dict:
    """Delete every input/<name> in ONE locked transaction, auditing
    file.deleted per file; returns {"deleted": int, "bytes": int,
    "failed": [name, ...]}.

    Bulk delete IS input (spec 8): same lock and same 409 as a single
    delete. Every name resolves to a real file BEFORE any unlink or audit
    row, so one unknown name aborts the batch with nothing touched;
    duplicate names collapse to one delete.

    Each file is its own savepoint (R1-92): its rows flush, then it is
    unlinked; an unlink that fails (permissions, a concurrent rename) rolls
    back that file's rows only and lands its name in `failed`, so the
    commit records exactly the files that are gone. A file that vanished
    meanwhile counts as deleted. Residual (spec A1, accepted, as in
    delete_file): a commit failure after the unlinks loses their rows.
    """
    unique_names = list(dict.fromkeys(_safe_name(project.input_file_type, n) for n in names))
    targets: dict[str, Path] = {}
    for name in unique_names:
        target = ws_path(project.id) / "input" / name
        if not target.is_file():
            raise InputFileNotFoundError(name)
        targets[name] = target
    deleted, total, failed = 0, 0, []
    async with input_mutation(session, project.id) as m:
        for name, target in targets.items():
            try:
                size = target.stat().st_size
            except FileNotFoundError:
                size = 0
            try:
                async with session.begin_nested():
                    await audit(
                        session,
                        actor_id,
                        "file.deleted",
                        "project",
                        str(project.id),
                        {"name": name, "size": size},
                    )
                    # file_tag_links cascade at the FK level (Task 1's model)
                    await session.execute(
                        delete(ProjectFile).where(
                            ProjectFile.project_id == project.id, ProjectFile.name == name
                        )
                    )
                    await m.apply(functools.partial(target.unlink, missing_ok=True))
            except OSError:
                failed.append(name)
                continue
            deleted += 1
            total += size
    return {"deleted": deleted, "bytes": total, "failed": failed}
