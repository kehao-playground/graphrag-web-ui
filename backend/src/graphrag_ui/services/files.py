"""Project input-file storage: whitelist, size/quota limits, path-safe writes.

Spec §6.5 (per-project format whitelist) and §10 (path traversal protection,
upload cap, per-project quota over input/ + output/). Services never touch
HTTP — error classes are translated to status codes by the api layer.
"""

import asyncio
import functools
import hashlib
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
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
from graphrag_ui.services.project_lock import input_mutation, lock_project
from graphrag_ui.services.projects import ws_path

# Upload whitelist keyed by project.input_file_type (spec §6.5):
# text → txt/md, csv → csv, json → json.
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "text": {".txt", ".md"},
    "csv": {".csv"},
    "json": {".json"},
}


class FileServiceError(Exception):
    """Invalid filename/extension — routes map to 400 (ApiError carries
    e.code/e.params so the client can localize)."""

    def __init__(self, code: str, detail: str, params: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.params = params


class FileTooLargeError(Exception):
    """Single file above upload_max_file_mb — routes map to 413."""

    def __init__(self, max_mb: int) -> None:
        super().__init__(f"file exceeds the {max_mb} MiB upload limit")
        self.code = "file_too_large"
        self.params = {"max_mb": max_mb}


class QuotaExceededError(Exception):
    """input/+output/ usage above project_quota_mb — routes map to 413."""

    def __init__(self, quota_mb: int) -> None:
        super().__init__(f"project storage quota of {quota_mb} MiB exceeded")
        self.code = "quota_exceeded"
        self.params = {"quota_mb": quota_mb}


class LocatorMismatch(Exception):
    """A {result_id, entry_id} locator failed one of its three bindings
    (spec 7.4). Carries no detail on purpose: unknown and mismatched must
    stay indistinguishable, so the route maps every failure to one fixed
    404 — never a 403, which would confirm the row exists."""


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
    """Streaming sha256; used by discovery and by the start snapshot, which
    both hash files nobody just uploaded."""
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
        raise FileNotFoundError(name)
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

    Three bindings, any mismatch -> LocatorMismatch: the result's run must
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
                    raise LocatorMismatch
    raise LocatorMismatch


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
    """Rows are input/ UNION the baseline's filenames, sorted by name.

    Reads the BASELINE SNAPSHOT ROW, never documents.parquet: recovery
    provenance was evaluated when the artifacts were produced, and
    re-deriving it here would bind the answer to today's configuration
    (spec 6.3). The only artifact touch is a stat on
    output/documents.parquet, which distinguishes unavailable_not_indexed
    from available.

    Returns {"files": [...], "ingest_check": str, "has_baseline": bool}. A
    name with no file behind it (a `removed` document) carries NULL
    size/modified_at/sha256 and no tags rather than invented values.
    """
    # Deferred: index_snapshots imports sha256_file from this module, so a
    # top-level import here would be circular.
    from graphrag_ui.services.index_snapshots import baseline_entries, baseline_row

    root = ws_path(project.id)
    scanned = await asyncio.to_thread(_scan_input, root / "input")
    row = await baseline_row(session, project.id)
    baseline = await baseline_entries(session, project.id)
    await _discover_untracked(session, project, scanned)

    file_rows = (
        (await session.execute(select(ProjectFile).where(ProjectFile.project_id == project.id)))
        .scalars()
        .all()
    )
    name_of = {f.id: f.name for f in file_rows}
    tags: dict[str, list[str]] = {name: [] for name in name_of.values()}
    for file_id, tag_name in (
        await session.execute(
            select(FileTagLink.file_id, FileTag.name)
            .join(FileTag, FileTag.id == FileTagLink.tag_id)
            .where(FileTag.project_id == project.id)
        )
    ).all():
        # Links cascade with their file row at the FK level, so a link whose
        # file is not among this project's loaded rows should not exist; the
        # guard keeps a stray one from failing the whole listing.
        if file_id in name_of:
            tags[name_of[file_id]].append(tag_name)
    for file_tags in tags.values():
        file_tags.sort()

    if row is None:
        check = IngestCheck.unavailable_no_baseline
    elif row.title_recovery == "unavailable_title_column":
        check = IngestCheck.unavailable_title_column
    elif not _documents_parquet_exists(root):
        check = IngestCheck.unavailable_not_indexed
    else:
        check = IngestCheck.available
    attributable = (
        AttributableTitles.of(row.attributable_titles)
        if row is not None and check == IngestCheck.available
        else AttributableTitles.unavailable()
    )

    files = []
    for name in sorted(set(scanned) | set(baseline)):
        on_disk = scanned.get(name)
        files.append(
            {
                "name": name,
                "size": on_disk[0] if on_disk else None,
                "modified_at": on_disk[1] if on_disk else None,
                "sha256": on_disk[2] if on_disk else None,
                "index_state": index_state(
                    name, on_disk[2] if on_disk else None, baseline, attributable
                ).value,
                "tags": tags.get(name, []) if on_disk else [],
            }
        )
    return {"files": files, "ingest_check": check.value, "has_baseline": row is not None}


def _scan_input(input_dir: Path) -> dict[str, tuple[int, str, str]]:
    """iterdir/stat/sha256 over input/ — {name: (size, modified_at iso,
    sha256)}. Hashing every file is unbounded (spec A4), so list_files runs
    this off the event loop via to_thread. Dotfiles are skipped: they are
    never valid uploads, and the only writer here (save_file) uses
    dot-prefixed tmp names during atomic writes."""
    if not input_dir.is_dir():
        return {}
    entries: dict[str, tuple[int, str, str]] = {}
    for p in input_dir.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()
        entries[p.name] = (
            st.st_size,
            datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            sha256_file(p),
        )
    return entries


async def _discover_untracked(
    session: AsyncSession, project: Project, scanned: Mapping[str, tuple[int, str, str]]
) -> None:
    """Insert a project_files row for every scanned name that has none.

    Files that predate this release have no row and nothing on disk records
    who uploaded them: discovery runs on the FIRST listing, with
    uploaded_by/uploaded_at NULL and discovered_at set. The name check runs
    under the project-row lock and the commit always happens, so concurrent
    listings cannot double-insert (uq_project_files_project_name backs the
    check) and the lock is released even when there is nothing to insert.
    """
    if not scanned:
        return
    await lock_project(session, project.id)
    known = set(
        (
            await session.execute(
                select(ProjectFile.name).where(ProjectFile.project_id == project.id)
            )
        ).scalars()
    )
    missing = [name for name in scanned if name not in known]
    if missing:
        session.add_all(
            ProjectFile(
                project_id=project.id,
                name=name,
                sha256=scanned[name][2],
                size=scanned[name][0],
                discovered_at=datetime.now(UTC),
            )
            for name in missing
        )
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

    FileNotFoundError is raised before any audit row; ProjectIndexingError
    (spec 5.2b) is raised inside the project lock, before any row or unlink.
    Residual (spec A1, accepted): a commit failure after the unlink loses
    the audit row.
    """
    name = _safe_name(project.input_file_type, filename)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        raise FileNotFoundError(name)
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
        raise FileNotFoundError(name)
    # A file nobody listed yet has no project_files row; tags attach to the
    # row, so discover it inline (the same shape _discover_untracked would
    # produce on the next listing). The hash is the unbounded walk listing
    # already does per file (spec A4) — run it off the lock.
    sha = await asyncio.to_thread(sha256_file, target)
    size = target.stat().st_size
    unique_tags = sorted(dict.fromkeys(tags))
    async with input_mutation(session, project.id, freeze=False):
        row = (
            await session.execute(
                select(ProjectFile).where(
                    ProjectFile.project_id == project.id, ProjectFile.name == name
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = ProjectFile(
                project_id=project.id,
                name=name,
                sha256=sha,
                size=size,
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
        raise FileNotFoundError(name)
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
            raise FileNotFoundError(name)
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
