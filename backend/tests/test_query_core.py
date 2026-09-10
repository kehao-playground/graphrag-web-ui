"""The shared query core (spec 7.2): the preamble and the tail are shared;
the two searches are not.

The limiter placement is the load-bearing assertion. If _execute_query
carried the limiter, a 20-question batch would consume an entire
interactive bucket and could be rejected mid-set, leaving a partial run -
which is the whole reason batch execution is a job.
"""

import uuid
from types import SimpleNamespace

import pandas as pd
import pytest

from graphrag_ui.adapters.models import Project
from graphrag_ui.config import get_settings
from graphrag_ui.services import query as query_service
from graphrag_ui.services.citations import Generation
from graphrag_ui.services.rate_limit import (
    QueryRateLimitedError,
    get_rate_limiter,
    reset_rate_limiter,
)

ANSWER = "首要原因是測試 [Data: Sources (2)]。"
SOURCES = pd.DataFrame({"id": [1, 2], "text": ["文字一", "文字二"]})
CHUNKS = ["The ", "Analytical ", "Engine [Data: Sources (2)]."]


class FakeAdapter:
    """Both seams the service can hit: search (run_query / the core) and
    stream (stream_query) — the core must not merge them."""

    async def search(self, method, config, frames, query, response_type):
        return ANSWER, {"sources": SOURCES}

    def stream(self, method, config, frames, query, response_type):
        async def gen():
            for chunk in CHUNKS:
                yield chunk

        return gen()


class FakeCache:
    def __init__(self):
        self.tables: list[str] = []

    async def get(self, root, table):
        self.tables.append(table)
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Stub config load (empty test workspaces have no settings.yaml); keep
    settings/limiter singletons from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    reset_rate_limiter()
    yield
    get_settings.cache_clear()
    reset_rate_limiter()


@pytest.fixture(autouse=True)
def _no_generation(monkeypatch):
    """These direct calls have no DB project behind them: G0 reads as "no
    baseline", which renders citations unlinked without touching the
    database (enrichment short-circuits on a null pointer)."""

    async def _read(_project_id):
        return Generation(None, 0, False)

    monkeypatch.setattr(query_service, "read_generation", _read)
    yield


@pytest.fixture
def project(monkeypatch, tmp_path):
    """Unsaved Project + hermetic workspace — the direct service calls below
    need no DB row and no real graphrag init (mirrors test_files.py)."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    try:
        yield Project(
            id=uuid.uuid4(), name="core", slug="core", owner_id=uuid.uuid4(), input_file_type="text"
        )
    finally:
        get_settings.cache_clear()


@pytest.fixture
def user():
    # run_query reads only str(user.id) — no DB row needed.
    return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture
def fake_adapter(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
    return adapter


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(query_service, "get_frame_cache", lambda: cache)
    return cache


async def test_execute_query_applies_no_limiter(project, fake_adapter, fake_cache):
    limiter = get_rate_limiter()
    for _ in range(limiter.limit_per_hour):
        limiter.check("u1", str(project.id))

    prepared = await query_service._prepare_query(project, "local")
    body = await query_service._execute_query(
        prepared, "local", "q", None, g0=Generation(None, 0, False)
    )
    assert body["answer"]


async def test_run_query_still_applies_the_limiter(project, user, fake_adapter):
    limiter = get_rate_limiter()
    for _ in range(limiter.limit_per_hour):
        limiter.check(str(user.id), str(project.id))

    with pytest.raises(QueryRateLimitedError):
        await query_service.run_query(project, user, "local", "q")


async def test_run_query_and_the_core_produce_identical_bodies(
    project, user, fake_adapter, fake_cache
):
    direct = await query_service.run_query(project, user, "local", "q")
    prepared = await query_service._prepare_query(project, "local")
    core = await query_service._execute_query(
        prepared, "local", "q", None, g0=Generation(None, 0, False)
    )
    assert direct["answer"] == core["answer"]
    assert direct["citations"] == core["citations"]
    assert set(direct["timings"]) == set(core["timings"])


async def test_prepare_query_reuses_a_caller_supplied_config(
    project, fake_adapter, fake_cache, monkeypatch
):
    """A run loads its configuration ONCE. settings.yaml and .env are frozen
    only during index/update, so a 200-question batch that re-read
    configuration per question could answer its first questions under one
    model and its last under another while presenting them as one run."""
    loads = {"n": 0}
    real = query_service.load_config

    def counting(root):
        loads["n"] += 1
        return real(root)

    monkeypatch.setattr(query_service, "load_config", counting)

    first = await query_service._prepare_query(project, "local")
    await query_service._prepare_query(project, "local", config=first.config)
    assert loads["n"] == 1


async def test_streaming_still_streams(project, user, fake_adapter, fake_cache):
    """Regression guard: stream_query must remain an async generator with
    chunk -> citations -> done, not a wrapper around _execute_query."""
    kinds = [kind async for kind, _ in query_service.stream_query(project, user, "local", "q")]
    assert kinds[0] == "chunk"
    assert kinds[-2:] == ["citations", "done"]
