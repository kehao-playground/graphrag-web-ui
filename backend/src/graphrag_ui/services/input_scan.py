"""What counts as an input file, and its hash: the input/ scan shared by the
Documents listing (file_listing) and the index start snapshot
(index_snapshots).

A leaf: it imports no other service, so both of those import it at top
level without a cycle (R1-07).
"""

import hashlib
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from graphrag_ui.adapters.models import ProjectFile

CHUNK_BYTES = 1024 * 1024  # streaming read granularity (bounded memory)


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file nobody just uploaded (scan_input on a
    cache miss, inline discovery in add_tags)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


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
