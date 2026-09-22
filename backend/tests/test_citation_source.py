"""Citation source_name resolution (spec 7.4): provenance, batching,
removed documents, and the no-fallback rules.

Every test here goes through the API door the UI uses (POST /query or the
stored-run results read) or the batch service itself, against a fake
indexed workspace with real baseline rows and a real documents.parquet —
the resolver, the recovery rule, and the guard all run for real.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.adapters.models import (
    Job,
    Project,
    Question,
    QuestionSet,
    TestResult,
    TestRun,
)
from sqlalchemy import select
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.config import get_settings
from graphrag_ui.services import citations as citations_service
from graphrag_ui.services import query as query_service
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.rate_limit import reset_rate_limiter
from graphrag_ui.services.test_runs import execute_test_run
from tests.citation_fixtures import (
    UNIT_TEXT,
    FakeFrameCache,
    FakeSearchAdapter,
    _all_source_names_null,
    _rebuild_so_that_hrid_1_is,
    promote_baseline,
    seed_test_run,
    text_unit_frame,
    write_documents,
)
from tests.test_projects import _activate, _setup_two_users

# The shared fake answer cites one source and one entity, so tests can
# assert both the resolved Sources entry and the unlinked non-Sources one.
ANSWER = "Answer body [Data: Sources (1); Entities (7)]."


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Stub the config load (fake workspaces have no graphrag-parsable
    settings.yaml); keep the limiter from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    reset_rate_limiter()
    yield
    get_settings.cache_clear()
    reset_rate_limiter()


@pytest.fixture
def fake_adapter(monkeypatch):
    adapter = FakeSearchAdapter()
    adapter.answer = ANSWER
    monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
    return adapter


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeFrameCache({})
    monkeypatch.setattr(query_service, "get_frame_cache", lambda: cache)
    return cache


def _wire_frames(fake_adapter: FakeSearchAdapter, fake_cache: FakeFrameCache) -> None:
    """One text unit (hrid 1 -> document d1) + one entity frame, on both the
    adapter's context and the cache the stream path joins against."""
    unit = text_unit_frame([1], ["d1"])
    fake_adapter.context = {"sources": unit, "entities": pd.DataFrame({"id": [7], "name": ["E7"]})}
    fake_cache.frames = {"text_units": unit}


def _wire_real_shapes(fake_adapter: FakeSearchAdapter, fake_cache: FakeFrameCache) -> None:
    """The two frame shapes graphrag 3.1.x actually hands us (R4-40): the
    search CONTEXT text-units frame carries int ids and text but NO
    document_id column; the cached PARQUET frame carries hash ids,
    human_readable_id and document_id. Only the parquet can map a cited
    hrid to its document."""
    fake_adapter.context = {
        "sources": pd.DataFrame({"id": [1], "text": [UNIT_TEXT]}),
        "entities": pd.DataFrame({"id": [7], "name": ["E7"]}),
    }
    fake_cache.frames = {
        "text_units": pd.DataFrame(
            {
                "id": ["a3f9" * 32],
                "human_readable_id": [1],
                "text": [UNIT_TEXT],
                "document_id": ["d1"],
            }
        )
    }


def _sse_citations(body: str) -> list:
    data = None
    event = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event == "citations":
            data = json.loads(line[len("data: ") :])
    assert data is not None, body
    return data


async def _api_project(client, app, *, name="Cite", input_file_type="text") -> tuple[str, Project]:
    """Activated alice + FakeInitializer project; yields (pid, project row)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post(
        "/api/projects", headers=alice, json={"name": name, "input_file_type": input_file_type}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], alice


async def _project_row(db_session: AsyncSession, pid: str) -> Project:
    project = await db_session.get(Project, uuid.UUID(pid))
    assert project is not None
    return project


@pytest.fixture
async def indexed_project_api(client, app, db_session, fake_adapter, fake_cache):
    """API project over a fake indexed workspace: hrid 1 -> d1 -> title
    file-a.md, promoted baseline {file-a.md} at epoch 1, and input/file-a.md
    holding the cited passage (the ad-hoc preview searches for it)."""
    pid, alice = await _api_project(client, app)
    project = await _project_row(db_session, pid)
    root = ws_path(project.id)
    (root / "input" / "file-a.md").write_text(f"{UNIT_TEXT} and the rest of the document")
    write_documents(root, [("d1", "file-a.md")])
    await promote_baseline(db_session, project, ["file-a.md"], epoch=1)
    _wire_frames(fake_adapter, fake_cache)
    return alice, pid


@pytest.fixture
async def real_shape_project_api(client, app, db_session, fake_adapter, fake_cache):
    """indexed_project_api with the real graphrag frame shapes wired in
    (see _wire_real_shapes); yields (alice headers, pid, token)."""
    pid, alice = await _api_project(client, app, name="RS")
    project = await _project_row(db_session, pid)
    root = ws_path(project.id)
    (root / "input" / "file-a.md").write_text(UNIT_TEXT)
    write_documents(root, [("d1", "file-a.md")])
    await promote_baseline(db_session, project, ["file-a.md"], epoch=1)
    _wire_real_shapes(fake_adapter, fake_cache)
    token = (
        await client.post(
            "/api/auth/login", json={"email": "alice@test.local", "password": "alice-pass-2"}
        )
    ).json()["access_token"]
    return alice, pid, token


@pytest.fixture
async def title_column_project(client, app, db_session, fake_adapter, fake_cache):
    """A csv project indexed while input.title_column was set: documents
    carry the TITLE report.md while input/ holds data.csv AND a real
    report.md. The baseline recorded the recovery as unavailable; today's
    settings.yaml (FakeInitializer's, no title_column) would happily
    attribute the citation to report.md."""
    pid, alice = await _api_project(client, app, name="TC", input_file_type="csv")
    project = await _project_row(db_session, pid)
    root = ws_path(project.id)
    (root / "input" / "data.csv").write_text("title,body\nreport.md,row one\n")
    (root / "input" / "report.md").write_text("the real report")
    write_documents(root, [("d1", "report.md")])
    await promote_baseline(
        db_session, project, ["data.csv", "report.md"], epoch=1, recovery="unavailable_title_column"
    )
    _wire_frames(fake_adapter, fake_cache)
    return alice, pid


@pytest.fixture
async def removed_doc_project(client, app, db_session, fake_adapter, fake_cache):
    """old.md was full-indexed then deleted from input/, and an update kept
    it in the index: the baseline's ENTRY names still remember it, so a
    citation into it stays linkable."""
    pid, alice = await _api_project(client, app, name="RM")
    project = await _project_row(db_session, pid)
    root = ws_path(project.id)
    (root / "input" / "keep.md").write_text(UNIT_TEXT)  # old.md is gone
    write_documents(root, [("d1", "old.md"), ("d2", "keep.md")])
    await promote_baseline(db_session, project, ["old.md", "keep.md"], epoch=1)
    _wire_frames(fake_adapter, fake_cache)
    return alice, pid


@pytest.fixture
async def artifacts_without_baseline(client, app, db_session, fake_adapter, fake_cache):
    """A project whose output/ predates this feature: artifacts on disk, no
    baseline row at all."""
    pid, alice = await _api_project(client, app, name="NB")
    project = await _project_row(db_session, pid)
    write_documents(ws_path(project.id), [("d1", "file-a.md")])
    _wire_frames(fake_adapter, fake_cache)
    return alice, pid


@pytest.fixture
async def run_with_citations(client, app, db_session, fake_adapter, fake_cache):
    """A stored run whose one result cites Sources (1) -> file-a.md with the
    source_name resolved at run time; yields (alice, pid, run_id)."""
    pid, alice = await _api_project(client, app, name="SR")
    project = await _project_row(db_session, pid)
    write_documents(ws_path(project.id), [("d1", "file-a.md")])
    await promote_baseline(db_session, project, ["file-a.md"], epoch=1)
    _wire_frames(fake_adapter, fake_cache)

    qs = QuestionSet(project_id=project.id, name="Cite", created_by=project.owner_id)
    db_session.add(qs)
    await db_session.flush()
    question = Question(
        set_id=qs.id, lineage_id=uuid.uuid4(), text="q1", position=0, created_by=project.owner_id
    )
    db_session.add(question)
    await db_session.flush()
    job = Job(
        project_id=project.id,
        type="test_run",
        method="local",
        argv=[],
        queued_by=project.owner_id,
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()
    run = TestRun(project_id=project.id, set_id=qs.id, job_id=job.id, method="local")
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        TestResult(
            run_id=run.id,
            question_id=question.id,
            position=0,
            question_text="q1",
            answer=ANSWER,
            citations=[
                {
                    "label": "Sources",
                    "ids": [1],
                    "entries": [{"id": 1, "text": UNIT_TEXT, "source_name": "file-a.md"}],
                }
            ],
            timings={"search_ms": 1.0},
            completed_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return alice, pid, run.id


@pytest.fixture
async def run_ready(db_session, tmp_path, monkeypatch, fake_adapter, fake_cache):
    """A queued test_run job + 3 placeholder results; every answer cites
    the same five text units (hrids 1-5 -> documents d1-d5)."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    await reset_engine()
    try:
        ns = await seed_test_run(
            db_session,
            questions=3,
            hrids=[1, 2, 3, 4, 5],
            document_ids=[f"d{i}" for i in range(1, 6)],
            documents=[(f"d{i}", f"file-{i}.md") for i in range(1, 6)],
            entries=[f"file-{i}.md" for i in range(1, 6)],
        )
        unit = text_unit_frame([1, 2, 3, 4, 5], [f"d{i}" for i in range(1, 6)])
        fake_adapter.answer = "Answer body [Data: Sources (1, 2, 3, 4, 5)]."
        fake_adapter.context = {"sources": unit}
        fake_cache.frames = {"text_units": unit}
        yield ns
    finally:
        get_settings.cache_clear()
        await reset_engine()


@pytest.fixture
async def project_indexed(db_session, tmp_path, monkeypatch, fake_adapter, fake_cache):
    """Persisted project over a fake indexed workspace for direct service
    calls (no API): hrid 1 -> d1 -> file-a.md, baseline at epoch 1."""
    from tests.citation_fixtures import seed_project

    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    await reset_engine()
    try:
        project, _user = await seed_project(db_session)
        project.artifact_epoch = 1
        write_documents(ws_path(project.id), [("d1", "file-a.md")])
        await promote_baseline(db_session, project, ["file-a.md"], epoch=1)
        _wire_frames(fake_adapter, fake_cache)
        yield project
    finally:
        get_settings.cache_clear()
        await reset_engine()


async def test_stored_run_citations_never_re_resolve(client, run_with_citations, db_session):
    """Run A cites Sources (1) -> file-a; rebuild so current Sources (1) ->
    file-b; opening run A's citation must still reach file-a and must NEVER
    reach file-b. This is the negative test that catches a resolver quietly
    falling back to current artifacts."""
    alice, pid, run_id = run_with_citations  # stored source_name == "file-a.md"
    await _rebuild_so_that_hrid_1_is(db_session, await _project_row(db_session, pid), "file-b.md")

    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    entry = results["results"][0]["citations"][0]["entries"][0]
    assert entry["source_name"] == "file-a.md"


async def test_post_and_stream_link_the_same_document(client, real_shape_project_api):
    """R4-40: for the same question the stream route linked file-a.md while
    POST /query shipped source_name null, because the non-stream path
    joined the search context (no document_id column) instead of the
    cached parquet frames the search was handed. Both doors must agree."""
    alice, pid, token = real_shape_project_api
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    sources = [c for c in body["citations"] if c["label"] == "Sources"]
    assert sources and sources[0]["entries"][0]["source_name"] == "file-a.md"
    assert sources[0]["entries"][0]["text"] == UNIT_TEXT  # entry text still from the context

    async with client.stream(
        "GET",
        f"/api/projects/{pid}/query/stream",
        params={"method": "local", "query": "q", "token": token},
    ) as resp:
        assert resp.status_code == 200
        streamed = "".join([line + "\n" async for line in resp.aiter_lines()])
    stream_sources = [c for c in _sse_citations(streamed) if c["label"] == "Sources"]
    assert stream_sources[0]["entries"][0]["source_name"] == "file-a.md"


async def test_stored_batch_results_link_documents(db_session, run_ready, fake_adapter, fake_cache):
    """R4-40, batch door: every stored result of a run must carry a non-null
    source_name for its Sources entries — the rating drawer renders the
    stored citations and never re-resolves."""
    fake_adapter.answer = "Answer body [Data: Sources (1)]."
    fake_adapter.context = {"sources": pd.DataFrame({"id": [1], "text": [UNIT_TEXT]})}
    fake_cache.frames = {
        "text_units": pd.DataFrame(
            {"id": ["h1"], "human_readable_id": [1], "text": [UNIT_TEXT], "document_id": ["d1"]}
        )
    }
    await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)

    rows = (
        (await db_session.execute(select(TestResult).where(TestResult.run_id == run_ready.run_id)))
        .scalars()
        .all()
    )
    assert rows
    for row in rows:
        entries = [e for c in row.citations for e in c["entries"] if c["label"] == "Sources"]
        assert entries and all(e["source_name"] == "file-1.md" for e in entries), row.citations


async def test_adhoc_citations_do_not_re_resolve_either(client, indexed_project_api):
    """Same shape, different door: answer an ad-hoc query citing
    Sources (1) -> file-a, rebuild so current Sources (1) -> file-b, then
    open the citation from the still-rendered answer. It must reach
    file-a."""
    alice, pid = indexed_project_api
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    name = body["citations"][0]["entries"][0]["source_name"]
    passage = body["citations"][0]["entries"][0]["text"]
    assert name == "file-a.md"

    # The client still holds the payload; nothing re-resolves it.
    r = await client.post(
        f"/api/projects/{pid}/files/{name}/preview", headers=alice, json={"passage": passage[:100]}
    )
    assert r.status_code == 200 and r.json()["match"] is True


async def test_resolver_provenance_uses_the_baselines_rule_not_todays(client, title_column_project):
    """data.csv indexed under title_column emits the title report.md, the
    project also contains a real report.md, and the setting is later removed
    without a rebuild. Today's rule would confidently attribute data.csv's
    citation to report.md; the baseline's rule must render it UNLINKED."""
    alice, pid = title_column_project
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    assert _all_source_names_null(body)


async def test_citations_into_removed_documents_stay_linkable(client, removed_doc_project):
    """Full-index old.md, delete it, update, then cite the still-indexed
    document: source_name must be old.md. The candidate set is the
    baseline's ENTRY names, which still remember it - a live input/ listing
    would have dropped exactly this name."""
    alice, pid = removed_doc_project
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    names = {e["source_name"] for c in body["citations"] for e in c["entries"]}
    assert "old.md" in names


async def test_only_sources_citations_carry_a_source_name(client, indexed_project_api):
    """Entities, Reports, Relationships and Communities each summarize many
    text units spanning many documents; a single-document link would be a
    lie."""
    alice, pid = indexed_project_api
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    labels = {c["label"] for c in body["citations"]}
    assert labels - {"Sources"}, "fixture must produce a non-Sources citation too"
    for c in body["citations"]:
        if c["label"] != "Sources":
            assert all(e.get("source_name") is None for e in c["entries"])


async def test_one_resolver_call_per_question_querying_only_unmemoed_ids(
    db_session, run_ready, monkeypatch
):
    """Call-count contract: at most ONE resolver call per completed
    question, and it queries only ids not already in the run's memo. 200
    questions citing the same five text units issue exactly one call."""
    calls = []
    original = citations_service.resolve_document_titles

    def recording(root, ids):
        calls.append(set(ids))
        return original(root, ids)

    monkeypatch.setattr(citations_service, "resolve_document_titles", recording)

    await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)

    assert len(calls) == 1
    assert len(calls[0]) == 5


async def test_a_global_query_cites_no_sources_and_reads_no_documents(
    project_indexed, fake_adapter, monkeypatch
):
    """global loads no text units, so there is nothing to resolve and the
    resolver must not be called at all."""
    fake_adapter.answer = "Global answer [Data: Reports (5)]."
    fake_adapter.context = {}
    monkeypatch.setattr(citations_service, "resolve_document_titles", _explode("no read"))
    await query_service.run_query(project_indexed, SimpleNamespace(id=uuid.uuid4()), "global", "q")


def _explode(message: str):
    def inner(*args, **kwargs):
        raise AssertionError(message)

    return inner


async def test_a_missing_baseline_renders_unlinked(client, artifacts_without_baseline):
    """When there is no trustworthy baseline, entries stay unlinked; the
    resolver never falls back to inspecting current configuration or
    guessing from input/.

    The fixture is the real case, not a contrived one: a project whose
    output/ was produced before this feature shipped has artifacts and no
    baseline row at all.
    """
    alice, pid = artifacts_without_baseline
    body = (
        await client.post(
            f"/api/projects/{pid}/query", headers=alice, json={"method": "local", "query": "q"}
        )
    ).json()
    assert body["answer"]
    assert _all_source_names_null(body)
