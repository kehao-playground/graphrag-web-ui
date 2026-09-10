"""The generation guard (spec 7.4).

Every case here is a barrier test on purpose. A test that only rebuilds
AFTER the response passes without the guard existing at all, and each of
the first three cases is passed by an implementation that is wrong in a
different way. The fourth is the one a G0/G1-only epoch comparison cannot
catch.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.adapters.models import Project
from graphrag_ui.config import get_settings
from graphrag_ui.services import citations as citations_service
from graphrag_ui.services import query as query_service
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.query import run_query, stream_query
from graphrag_ui.services.rate_limit import reset_rate_limiter
from tests.citation_fixtures import (
    FakeFrameCache,
    FakeSearchAdapter,
    _all_source_names_null,
    _promote_new_baseline,
    _run_index_to_failure,
    _run_index_to_success,
    _start_index_job,
    promote_baseline,
    seed_project,
    seed_test_run,
    text_unit_frame,
    write_documents,
)
from tests.test_test_runs import _results

ANSWER = "Answer body [Data: Sources (1)]."


class _Parked:
    """reached resolves when the parked query reaches the documents read;
    release() lets it through."""

    def __init__(self) -> None:
        self.reached: asyncio.Future = asyncio.get_running_loop().create_future()
        self._release = asyncio.Event()

    def release(self) -> None:
        self._release.set()


@asynccontextmanager
async def _park_between_steps() -> AsyncIterator[_Parked]:
    """Park a query exactly between step 3 and step 4 of the normative
    sequence (spec 7.4): the wrapper replaces the resolver, signals, and
    waits. Parking anywhere else (before the frame load, or after G1)
    tests nothing: an implementation with no guard at all would pass. The
    original is restored on exit."""
    parked = _Parked()
    original = citations_service.resolve_document_titles

    async def wrapper(root, document_ids):
        parked.reached.set_result(None)
        await parked._release.wait()
        return original(root, document_ids)

    citations_service.resolve_document_titles = wrapper
    try:
        yield parked
    finally:
        citations_service.resolve_document_titles = original


@pytest.fixture
def park_before_documents_read():
    """The parking context manager, injected so a test can hold a query
    open exactly across the documents read (spec 7.4's guarded interval)."""
    return _park_between_steps


@pytest.fixture
async def project_indexed(db_session: AsyncSession, tmp_path, monkeypatch):
    """Persisted project over a fake indexed workspace (hrid 1 -> document
    d1 -> title file-a.md), promoted baseline {file-a.md} at epoch 1. The
    query seams are faked; the resolver, baseline reads, and both
    generation reads run for real."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    await reset_engine()
    reset_rate_limiter()
    try:
        project, _user = await seed_project(db_session)
        project.artifact_epoch = 1
        write_documents(ws_path(project.id), [("d1", "file-a.md")])
        await promote_baseline(db_session, project, ["file-a.md"], epoch=1)

        unit = text_unit_frame([1], ["d1"])
        adapter = FakeSearchAdapter()
        adapter.answer = ANSWER
        adapter.context = {"sources": unit}
        monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
        monkeypatch.setattr(
            query_service, "get_frame_cache", lambda: FakeFrameCache({"text_units": unit})
        )
        monkeypatch.setattr(query_service, "load_config", lambda root: object())
        yield project
    finally:
        get_settings.cache_clear()
        await reset_engine()


@pytest.fixture
def user():
    # run_query reads only str(user.id) — no DB row needed.
    return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture
async def run_ready(db_session: AsyncSession, tmp_path, monkeypatch):
    """A queued test_run job + 3 placeholder results over the same fake
    indexed workspace (every answer cites Sources (1))."""

    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    await reset_engine()
    reset_rate_limiter()
    try:
        ns = await seed_test_run(
            db_session,
            questions=3,
            hrids=[1],
            document_ids=["d1"],
            documents=[("d1", "file-a.md")],
            entries=["file-a.md"],
        )
        unit = text_unit_frame([1], ["d1"])
        adapter = FakeSearchAdapter()
        adapter.answer = ANSWER
        adapter.context = {"sources": unit}
        monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
        monkeypatch.setattr(
            query_service, "get_frame_cache", lambda: FakeFrameCache({"text_units": unit})
        )
        monkeypatch.setattr(query_service, "load_config", lambda root: object())
        yield ns
    finally:
        get_settings.cache_clear()
        await reset_engine()


async def test_a_promotion_between_the_frame_load_and_the_documents_read_withholds_links(
    project_indexed, user, park_before_documents_read, db_session
):
    """Case 1. The answer must still be returned - only the links are
    withheld."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _promote_new_baseline(
            db_session, project_indexed, ["file-b.md"], [("d1", "file-b.md")]
        )
        parked.release()
        body = await task

    assert body["answer"]
    assert all(
        e["source_name"] is None
        for c in body["citations"]
        if c["label"] == "Sources"
        for e in c["entries"]
    )


async def test_an_index_starting_in_that_interval_withholds_links(
    project_indexed, user, park_before_documents_read, db_session
):
    """Case 2, which a G1-at-frame-load implementation fails: an index
    starts without promoting a baseline, so the pointer and the epoch at G1
    would both look unchanged to a guard that closed too early. G1 must see
    the ACTIVE JOB and withhold."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _start_index_job(db_session, project_indexed)  # queued, never finishes
        parked.release()
        body = await task

    assert _all_source_names_null(body)


async def test_a_failed_index_inside_the_interval_withholds_links(
    project_indexed, user, park_before_documents_read, db_session
):
    """Case 3, the ABA control, which pointer-and-active-job comparison
    alone cannot catch: the index runs to completion AND FAILS, rewriting
    documents.parquet on its way. At G1 the pointer is unmoved and no job is
    active, yet links must still be withheld - only artifact_epoch
    distinguishes it."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _run_index_to_failure(db_session, project_indexed)  # bumps epoch, promotes nothing
        parked.release()
        body = await task

    assert _all_source_names_null(body)


async def test_a_brand_new_query_after_a_failed_index_still_withholds_links(
    project_indexed, user, db_session
):
    """Case 4, which a G0/G1-only epoch comparison cannot catch: let that
    same failed index finish completely, THEN start a brand-new query. G0
    and G1 agree on pointer and epoch and neither sees an active job, yet
    links must still be withheld - only
    projects.artifact_epoch == baseline.artifact_epoch rejects it."""
    await _run_index_to_failure(db_session, project_indexed)

    body = await run_query(project_indexed, user, "local", "q")
    assert body["answer"]
    assert _all_source_names_null(body)


async def test_a_successful_full_index_restores_links(project_indexed, user, db_session):
    """The conservatism must be recoverable, in both of the last two cases."""
    await _run_index_to_failure(db_session, project_indexed)
    assert _all_source_names_null(await run_query(project_indexed, user, "local", "q"))

    await _run_index_to_success(db_session, project_indexed, ["file-a.md"], [("d1", "file-a.md")])
    body = await run_query(project_indexed, user, "local", "q")
    assert any(
        e["source_name"] is not None
        for c in body["citations"]
        if c["label"] == "Sources"
        for e in c["entries"]
    )


async def test_the_streaming_path_is_guarded_identically(
    project_indexed, user, park_before_documents_read, db_session
):
    """The SSE route is the path the UI actually uses; a guard on run_query
    alone would leave the real one open."""
    async with park_before_documents_read() as parked:

        async def drain():
            return [k_v async for k_v in stream_query(project_indexed, user, "local", "q")]

        task = asyncio.create_task(drain())
        await parked.reached
        await _run_index_to_failure(db_session, project_indexed)
        parked.release()
        events = await task

    citations = next(v for k, v in events if k == "citations")
    assert any(k == "chunk" for k, _ in events)  # the answer still streamed
    assert all(
        e["source_name"] is None for c in citations if c["label"] == "Sources" for e in c["entries"]
    )


async def test_a_batch_run_is_guarded_identically(
    db_session, run_ready, park_before_documents_read
):
    """services/test_runs.py persists what it resolved, so an unguarded
    batch would write the wrong filename into history permanently."""
    from graphrag_ui.services.test_runs import execute_test_run

    async with park_before_documents_read() as parked:
        task = asyncio.create_task(
            execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
        )
        await parked.reached
        project = await db_session.get(Project, run_ready.project_id)
        assert project is not None
        # No job row: the run's own test_run job holds the
        # one-active-per-project slot, and the guard reads the epoch.
        await _run_index_to_failure(db_session, project, job_row=False)
        parked.release()
        await task

    rows = await _results(db_session, run_ready.run_id)
    assert all(r.answer for r in rows)
    assert all(
        e["source_name"] is None
        for r in rows
        for c in (r.citations or [])
        if c["label"] == "Sources"
        for e in c["entries"]
    )
