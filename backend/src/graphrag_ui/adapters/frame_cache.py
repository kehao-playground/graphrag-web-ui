"""Per-project parquet DataFrame cache with LRU byte-budget eviction."""

import asyncio
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import pandas as pd

from graphrag_ui.config import get_settings

# Mode -> parquet tables required by graphrag.api search (spec §6.4); the
# one map, read by graphrag_search to wire the search call (R1-37).
_LOCAL_TABLES = ("entities", "communities", "community_reports", "text_units", "relationships")
TABLES: dict[str, tuple[str, ...]] = {
    "basic": ("text_units",),
    "local": _LOCAL_TABLES,
    "drift": _LOCAL_TABLES,
    "global": ("entities", "communities", "community_reports"),
}


def tables_for(method: str) -> tuple[str, ...]:
    """Return the parquet table names needed for a query method."""
    try:
        return TABLES[method]
    except KeyError:
        raise ValueError(f"unknown query method: {method!r}") from None


class WorkspaceNotIndexedError(RuntimeError):
    """Raised when a workspace has no indexed parquet output yet."""


def _frame_bytes(df: pd.DataFrame) -> int:
    return int(df.memory_usage(deep=True).sum())


class FrameCache:
    """LRU cache of DataFrames keyed by (root, table).

    Validity key is (path, mtime_ns, size): a changed parquet file is
    re-read on the next get(). Byte accounting uses deep memory usage;
    on insert, LRU entries are evicted until the total fits the budget.
    """

    def __init__(self, budget_bytes: int) -> None:
        self.budget_bytes = budget_bytes
        # (root_str, table) -> (validity_key, DataFrame, deep bytes at insert);
        # the size is kept so eviction never walks the frame again (R1-103)
        self._entries: OrderedDict[tuple[str, str], tuple[tuple, pd.DataFrame, int]] = OrderedDict()
        self._bytes = 0

    async def get(self, root: Path, table: str) -> pd.DataFrame:
        path = root / "output" / f"{table}.parquet"
        key = (str(root), table)
        validity = self._validity(path)
        entry = self._entries.get(key)
        if entry is not None:
            if validity is not None and entry[0] == validity:
                self._entries.move_to_end(key)
                return entry[1]
            self._remove(key)
        if validity is None:
            raise WorkspaceNotIndexedError("not indexed yet — run an indexing job first")
        try:
            df = await asyncio.to_thread(pd.read_parquet, path)
        except FileNotFoundError as exc:  # removed between the stat and the read
            raise WorkspaceNotIndexedError("not indexed yet — run an indexing job first") from exc
        self._insert(key, validity, df)
        return df

    def frames_bytes(self) -> int:
        return self._bytes

    def invalidate(self, root: Path) -> None:
        root_str = str(root)
        for key in [k for k in self._entries if k[0] == root_str]:
            self._remove(key)

    @staticmethod
    def _validity(path: Path) -> tuple | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return (path, stat.st_mtime_ns, stat.st_size)

    def _remove(self, key: tuple[str, str]) -> None:
        self._bytes -= self._entries.pop(key)[2]

    def _insert(self, key: tuple[str, str], validity: tuple, df: pd.DataFrame) -> None:
        if key in self._entries:
            # Same-key overwrite (concurrent miss): release the superseded
            # entry's bytes first or they stay counted on top of the new one.
            self._remove(key)
        size = _frame_bytes(df)
        self._entries[key] = (validity, df, size)
        self._bytes += size
        # Evict LRU until within budget. The incoming frame itself is kept
        # even when it alone exceeds the budget: a single oversized table is
        # still queryable (one-shot cost) rather than a hard failure.
        while self._bytes > self.budget_bytes and len(self._entries) > 1:
            self._remove(next(iter(self._entries)))


@lru_cache
def get_frame_cache() -> FrameCache:
    """Module singleton sized from settings; reset via reset_frame_cache."""
    return FrameCache(get_settings().query_cache_mb * 1024 * 1024)


def reset_frame_cache() -> None:
    """Test hygiene: drop the singleton (pair with get_settings.cache_clear)."""
    get_frame_cache.cache_clear()
