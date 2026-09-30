"""Filesystem measurements shared by quota, cache, preflight and readiness.
Sync helpers run inside one to_thread hop at their callers (spec A4)."""

import asyncio
import shutil
from pathlib import Path

from graphrag_ui.services.projects import workspaces_root

MIB = 1024 * 1024


def tree_bytes(path: Path, *, skip_prefix: str | None = None) -> int:
    """Recursive byte size of the files under `path`; 0 when it does not
    exist yet. Files whose name starts with `skip_prefix` are not counted."""
    if not path.exists():
        return 0
    return sum(
        p.stat().st_size
        for p in path.rglob("*")
        if p.is_file() and not (skip_prefix and p.name.startswith(skip_prefix))
    )


def _free_bytes() -> int:
    # Measure the workspaces ROOT (spec §6.1), not a possibly-missing
    # project dir; create the root if needed so disk_usage has a target.
    root = workspaces_root()
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free


async def workspaces_free_bytes() -> int:
    """Free bytes on the volume holding the workspaces root."""
    return await asyncio.to_thread(_free_bytes)
