"""Project input-file storage: whitelist, size/quota limits, path-safe
writes and deletes.

Spec §6.5 (per-project format whitelist) and §10 (path traversal protection,
upload cap, per-project quota over input/ + output/). Services never touch
HTTP — error classes are translated to status codes by the api layer.

The read side lives beside it (R1-07): file_listing (the Documents
listing), file_preview (preview and citation locators), file_tags, and the
leaf input_scan (what counts as an input file, and its hash).
"""

import asyncio
import functools
import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, ProjectFile
from graphrag_ui.config import get_settings
from graphrag_ui.services.audit import audit
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.input_scan import CHUNK_BYTES
from graphrag_ui.services.project_lock import input_mutation
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


_MIB = 1024 * 1024


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


def input_file(project: Project, filename: str) -> tuple[str, Path]:
    """The validated name and its existing path under input/ — the one
    path-safety sequence every read or delete of a named file goes through
    (R1-29). A `removed` row has no file behind it, so it is not found
    either."""
    name = _safe_name(project.input_file_type, filename)
    target = ws_path(project.id) / "input" / name
    if not target.is_file():
        raise InputFileNotFoundError(name)
    return name, target


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
            while chunk := await source.read(CHUNK_BYTES):
                size += len(chunk)
                if size > max_file_bytes():
                    raise FileTooLargeError(get_settings().upload_max_file_mb)
                h.update(chunk)
                await asyncio.to_thread(out.write, chunk)
        # The quota needs the final size, so _commit_upload checks it once
        # the stream is consumed, inside the lock and before any row.
        await _commit_upload(
            session, project, name, size, h.hexdigest(), actor_id, tmp, input_dir / name
        )
        return name, size
    finally:
        tmp.unlink(missing_ok=True)  # no-op after a successful replace


async def delete_file(
    session: AsyncSession, project: Project, filename: str, actor_id: uuid.UUID | None
) -> int:
    """Remove input/<name> AND audit it, one transaction (spec A1); returns
    the removed file's size. The single-name case of bulk_delete (R1-93),
    with the same errors: InputFileNotFoundError before any row,
    ProjectIndexingError (spec 5.2b) inside the project lock. An unlink
    that fails rolls its rows back and is re-raised here, since a single
    delete has no `failed` list to report it in.
    """
    result = await bulk_delete(session, project, [filename], actor_id)
    if result["failed"]:
        raise OSError(f"could not delete input file {result['failed'][0]!r}")
    return result["bytes"]


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
    meanwhile counts as deleted. Residual (spec A1, accepted): a
    commit failure after the unlinks loses their rows.
    """
    targets = dict(input_file(project, n) for n in names)
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
