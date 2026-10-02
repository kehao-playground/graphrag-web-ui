"""Tests for the adapters-level parquet DataFrame LRU cache."""

from pathlib import Path

import pandas as pd
import pytest

from graphrag_ui.adapters import frame_cache as frame_cache_module
from graphrag_ui.adapters.frame_cache import (
    TABLES,
    FrameCache,
    WorkspaceNotIndexedError,
    get_frame_cache,
    reset_frame_cache,
    tables_for,
)


def _write_parquet(path: Path, n_rows: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"id": list(range(n_rows)), "text": [f"row {i}" for i in range(n_rows)]}
    ).to_parquet(path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _root = tmp_path / "workspace"
    for table in ("text_units", "entities", "communities"):
        _write_parquet(_root / "output" / f"{table}.parquet")
    return _root


async def test_hit_returns_same_object(root: Path) -> None:
    cache = FrameCache(budget_bytes=10_000_000)
    first = await cache.get(root, "text_units")
    second = await cache.get(root, "text_units")
    assert first is second


async def test_mtime_change_reloads(root: Path) -> None:
    cache = FrameCache(budget_bytes=10_000_000)
    path = root / "output" / "text_units.parquet"
    first = await cache.get(root, "text_units")
    # Bump mtime without changing content.
    import os

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    second = await cache.get(root, "text_units")
    assert first is not second


async def test_size_change_reloads(root: Path) -> None:
    cache = FrameCache(budget_bytes=10_000_000)
    first = await cache.get(root, "text_units")
    _write_parquet(root / "output" / "text_units.parquet", n_rows=50)
    second = await cache.get(root, "text_units")
    assert first is not second
    assert len(second) == 50


async def test_budget_evicts_oldest_lru(root: Path) -> None:
    small = pd.DataFrame({"id": [1, 2, 3], "text": ["a", "b", "c"]})
    frame_bytes = int(small.memory_usage(deep=True).sum())
    # Budget fits only ~1.5 frames.
    cache = FrameCache(budget_bytes=int(frame_bytes * 1.5))
    a = await cache.get(root, "text_units")
    b = await cache.get(root, "entities")
    c = await cache.get(root, "communities")
    assert a is not None and b is not None and c is not None
    # Oldest (text_units) evicted; total within budget + largest single frame.
    assert cache._bytes <= int(frame_bytes * 1.5) + frame_bytes
    assert (await cache.get(root, "text_units")) is not a  # reloaded


async def test_insert_overwrite_releases_superseded_bytes(root: Path) -> None:
    """Concurrent-miss overwrite (same key inserted twice without a remove in
    between) must not double-count bytes: the superseded entry's size is
    released before the replacement is added."""
    cache = FrameCache(budget_bytes=10_000_000)
    await cache.get(root, "text_units")
    replacement = pd.DataFrame({"id": [1], "text": ["x" * 100], "pad": ["y" * 100]})
    replacement_bytes = int(replacement.memory_usage(deep=True).sum())
    # replacement differs from the loaded frame, so the asserts below bite
    assert cache._bytes != replacement_bytes
    cache._insert((str(root), "text_units"), (None,), replacement)
    # Only the replacement entry remains in the accounting.
    assert cache._bytes == replacement_bytes
    assert cache._entries[(str(root), "text_units")][1] is replacement


async def test_missing_file_raises_not_indexed(tmp_path: Path) -> None:
    cache = FrameCache(budget_bytes=10_000_000)
    with pytest.raises(WorkspaceNotIndexedError) as exc:
        await cache.get(tmp_path / "empty", "text_units")
    assert str(exc.value) == "not indexed yet — run an indexing job first"


async def test_frame_sizes_are_measured_once_per_insert(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-103: deep memory_usage walks every object cell; eviction reuses
    the size taken at insert."""
    calls = {"n": 0}
    real = frame_cache_module._frame_bytes

    def counting(df: pd.DataFrame) -> int:
        calls["n"] += 1
        return real(df)

    monkeypatch.setattr(frame_cache_module, "_frame_bytes", counting)
    one = real(pd.read_parquet(root / "output" / "text_units.parquet"))
    cache = FrameCache(budget_bytes=2 * one)
    for table in ("text_units", "entities", "communities"):  # third evicts the first
        await cache.get(root, table)
    assert calls["n"] == 3
    assert cache._bytes == 2 * one  # the evicted frame's size came off without a re-measure


async def test_a_miss_stats_the_file_once(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = FrameCache._validity

    def counting(path: Path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(FrameCache, "_validity", staticmethod(counting))
    await FrameCache(budget_bytes=10_000_000).get(root, "text_units")
    assert calls["n"] == 1


def test_tables_for_matrix() -> None:
    assert tables_for("basic") == ("text_units",)
    assert tables_for("local") == (
        "entities",
        "communities",
        "community_reports",
        "text_units",
        "relationships",
    )
    assert tables_for("drift") == TABLES["local"]
    assert tables_for("global") == ("entities", "communities", "community_reports")
    with pytest.raises(ValueError):
        tables_for("unknown")


def test_singleton_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphrag_ui.config import get_settings

    reset_frame_cache()
    get_settings.cache_clear()
    monkeypatch.setenv("QUERY_CACHE_MB", "7")
    try:
        cache = get_frame_cache()
        assert cache is get_frame_cache()
        assert cache.budget_bytes == 7 * 1024 * 1024
    finally:
        reset_frame_cache()
        get_settings.cache_clear()
