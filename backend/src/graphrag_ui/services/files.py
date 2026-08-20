"""Project input-file storage: whitelist, size/quota limits, path-safe writes.

Spec §6.5 (per-project format whitelist) and §10 (path traversal protection,
upload cap, per-project quota over input/ + output/). Services never touch
HTTP — error classes are translated to status codes by the api layer.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from graphrag_ui.adapters.models import Project
from graphrag_ui.config import get_settings
from graphrag_ui.services.projects import _ws_path

# Upload whitelist keyed by project.input_file_type (spec §6.5):
# text → txt/md, csv → csv, json → json.
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "text": {".txt", ".md"}, "csv": {".csv"}, "json": {".json"},
}


class FileServiceError(Exception):
    """Invalid filename/extension — routes map to 400."""


class FileTooLargeError(Exception):
    """Single file above upload_max_file_mb — routes map to 413."""


class QuotaExceededError(Exception):
    """input/+output/ usage above project_quota_mb — routes map to 413."""


_MIB = 1024 * 1024


def _safe_name(project_input_file_type: str, filename: str) -> str:
    """Validate a client-supplied filename; returns the name unchanged.

    Every rejection happens before the name ever reaches the filesystem, so
    no path variant (separator, '..', leading dot) can escape input/.
    """
    if not filename:
        raise FileServiceError("filename must not be empty")
    if len(filename) > 255:
        raise FileServiceError("filename exceeds 255 characters")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise FileServiceError("filename must not contain path separators or '..'")
    if filename.startswith("."):
        raise FileServiceError("filename must not start with '.'")
    allowed = ALLOWED_EXTENSIONS.get(project_input_file_type, set())
    ext = Path(filename).suffix
    if ext not in allowed:
        raise FileServiceError(
            f"extension '{ext or '(none)'}' not allowed for "
            f"input_file_type '{project_input_file_type}'")
    return filename


def _dir_size(path: Path) -> int:
    """Recursive byte size; 0 when the directory does not exist yet."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def quota_bytes() -> int:
    return get_settings().project_quota_mb * _MIB


def max_file_bytes() -> int:
    return get_settings().upload_max_file_mb * _MIB


def usage_bytes(project: Project) -> int:
    """input/ + output/ both count against the project quota (spec §10)."""
    root = _ws_path(project.id)
    return _dir_size(root / "input") + _dir_size(root / "output")


async def save_file(project: Project, filename: str, data: bytes) -> str:
    """Write data to input/<name>; returns the stored name.

    Overwriting an existing name is allowed (idempotent re-upload).
    """
    name = _safe_name(project.input_file_type, filename)
    if len(data) > max_file_bytes():
        raise FileTooLargeError(
            f"file exceeds the {get_settings().upload_max_file_mb} MiB upload limit")
    if usage_bytes(project) + len(data) > quota_bytes():
        raise QuotaExceededError(
            f"project storage quota of {get_settings().project_quota_mb} MiB exceeded")
    input_dir = _ws_path(project.id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    # tmp+replace keeps writes atomic: readers never see a partial file. The
    # tmp name is dot-prefixed so a concurrent listing never surfaces it.
    tmp = input_dir / f".tmp-{uuid.uuid4().hex}"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, input_dir / name)
    finally:
        tmp.unlink(missing_ok=True)  # no-op after a successful replace
    return name


async def list_files(project: Project) -> list[dict]:
    """[{name, size, modified_at}] sorted by name.

    Dotfiles are skipped: they are never valid uploads, and the only writer
    here (save_file) uses dot-prefixed tmp names during atomic writes.
    """
    input_dir = _ws_path(project.id) / "input"
    if not input_dir.exists():
        return []
    entries = []
    for p in input_dir.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()
        entries.append({
            "name": p.name,
            "size": st.st_size,
            "modified_at": datetime.fromtimestamp(
                st.st_mtime, tz=UTC).isoformat(),
        })
    entries.sort(key=lambda e: e["name"])
    return entries


async def delete_file(project: Project, filename: str) -> int:
    """Remove input/<name>; returns the removed file's size for the audit log."""
    name = _safe_name(project.input_file_type, filename)
    target = _ws_path(project.id) / "input" / name
    if not target.is_file():
        raise FileNotFoundError(name)
    size = target.stat().st_size
    target.unlink()
    return size
