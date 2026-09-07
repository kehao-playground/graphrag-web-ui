"""Project input-file storage: whitelist, size/quota limits, path-safe writes.

Spec §6.5 (per-project format whitelist) and §10 (path traversal protection,
upload cap, per-project quota over input/ + output/). Services never touch
HTTP — error classes are translated to status codes by the api layer.
"""

import asyncio
import hashlib
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import FileTag, FileTagLink, Project, ProjectFile
from graphrag_ui.config import get_settings
from graphrag_ui.domain.files import AttributableTitles, IngestCheck, index_state
from graphrag_ui.services.audit import audit
from graphrag_ui.services.project_lock import assert_input_unfrozen, lock_project
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


def _dir_size(path: Path) -> int:
    """Recursive byte size; 0 when the directory does not exist yet."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def sha256_file(path: Path) -> str:
    """Streaming sha256; used by discovery and by the start snapshot, which
    both hash files nobody just uploaded."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


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
    """The committing transaction: lock, re-check the freeze, write the audit
    row and project_files, rename, commit.

    The lock is taken HERE and not around the upload stream: holding it for
    the whole of a multi-gigabyte upload would block job enqueue for that
    long. Taking it only for the rename keeps the window short and still
    serializes, because enqueue takes the same lock (spec 5.2b).
    """
    await lock_project(session, project.id)
    await assert_input_unfrozen(session, project.id)
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
    await session.flush()
    _replace_into_input(tmp, target)
    await session.commit()


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
    the input freeze, flushes the file.uploaded + project_files rows and
    only then renames, so any failure (stream, cap, quota, freeze, rename)
    rolls the rows back and leaves no partial file behind.
    """
    name = _safe_name(project.input_file_type, filename)
    # Usage snapshot BEFORE the tmp file appears in input/: the quota check
    # runs after streaming (it needs the final size) and must not count the
    # in-flight tmp file itself. Snapshot-first matches the old
    # check-then-write semantics, overwrite case included (existing file counted).
    base_usage = await usage_bytes(project)
    input_dir = ws_path(project.id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    # tmp+replace keeps writes atomic: readers never see a partial file. The
    # tmp name is dot-prefixed so a concurrent listing never surfaces it.
    tmp = input_dir / f".tmp-{uuid.uuid4().hex}"
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
        # Quota check needs the final size, so it runs after the stream is
        # fully consumed, against the pre-write usage snapshot — before the
        # audit row, so an over-quota upload leaves no trace.
        if base_usage + size > quota_bytes():
            raise QuotaExceededError(get_settings().project_quota_mb)
        await _commit_upload(
            session, project, name, size, h.hexdigest(), actor_id, tmp, input_dir / name
        )
        return name, size
    except Exception:
        await session.rollback()
        raise
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
            tags[file_id].append(tag_name)
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
    try:
        await lock_project(session, project.id)
        await assert_input_unfrozen(session, project.id)
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
        await session.flush()
        target.unlink()
        await session.commit()
        return size
    except Exception:
        await session.rollback()
        raise
