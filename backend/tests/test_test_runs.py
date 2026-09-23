"""Batch test runs (spec 5.3/7.2/7.3).

POST /api/projects/{pid}/test-runs and GET /api/test-runs/{id}/results land
in Task 6; until then the enqueue-side tests drive enqueue_run at the
service level and assert against the DB — Task 6's API tests re-pin these
through HTTP. The two lock-barrier interleavings already call the services
directly, so they are unaffected.
"""

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters import jobs_repo, models
from graphrag_ui.adapters.db import (
    get_session_factory,
    make_engine,
    make_session_factory,
    reset_engine,
)
from graphrag_ui.adapters.models import Job, Project, Question, QuestionSet, User
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.config import get_settings
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services import query as query_service
from graphrag_ui.services import questions as questions_service
from graphrag_ui.services import test_runs as test_runs_service
from graphrag_ui.services.jobs import JobConflictError
from tests.test_projects import _activate, _setup_two_users
from tests.test_questions import _add_question

ANSWER = "首要原因是測試 [Data: Sources (2)]。"
SOURCES = pd.DataFrame({"id": [1, 2], "text": ["文字一", "文字二"]})


class FakeAdapter:
    """The batch's only search seam. fail_on(fragment) makes every question
    whose text contains the fragment raise — one failing cell, not a failed
    run (spec 7.3)."""

    def __init__(self) -> None:
        self._fail_on: list[str] = []
        self.queries: list[str] = []

    def fail_on(self, fragment: str) -> None:
        self._fail_on.append(fragment)

    async def search(self, method, config, frames, query, response_type):
        self.queries.append(query)
        if any(fragment in query for fragment in self._fail_on):
            raise RuntimeError(f"planned search failure: {query}")
        return ANSWER, {"sources": SOURCES}


class FakeCache:
    def __init__(self) -> None:
        self.tables: list[str] = []

    async def get(self, root, table):
        self.tables.append(table)
        return pd.DataFrame()


def _workspace(root: Path, *, settings: bytes | None, env: bytes | None = None) -> Path:
    """A workspace dir; None means the file is absent (missing vs empty is
    exactly what the framing digest must distinguish)."""
    root.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        (root / "settings.yaml").write_bytes(settings)
    if env is not None:
        (root / ".env").write_bytes(env)
    return root


def _counting(counter: dict):
    def load(root):
        counter["n"] += 1
        return object()

    return load


async def _results(db_session: AsyncSession, run_id: uuid.UUID) -> list[models.TestResult]:
    rows = await db_session.execute(
        select(models.TestResult)
        .where(models.TestResult.run_id == run_id)
        .order_by(models.TestResult.position)
    )
    return list(rows.scalars().all())


async def _queue_job(db_session: AsyncSession, project: Project, *, type_: str = "index") -> Job:
    job = Job(
        project_id=project.id,
        type=type_,
        method="standard",
        argv=["index", "--root", "/ws", "--method", "standard"],
        queued_by=project.owner_id,
        status="queued",
    )
    db_session.add(job)
    await db_session.commit()
    return job


async def _owner_of(db_session: AsyncSession, pid: str) -> tuple[Project, User]:
    project = await db_session.get(Project, uuid.UUID(pid))
    assert project is not None
    owner = await db_session.get(User, project.owner_id)
    assert owner is not None
    return project, owner


@pytest.fixture(autouse=True)
def _config_seam(monkeypatch):
    """Stub the config load (test workspaces have no graphrag-parsable
    settings.yaml); keep settings singletons from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


@pytest.fixture
async def project_with_set(client, app):
    """alice owns a project with one (empty) question set."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post(
        "/api/projects", headers=alice, json={"name": "Questions", "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/question-sets", headers=alice, json={"name": "Smoke"}
    )
    assert r.status_code == 201, r.text
    return alice, pid, r.json()["id"]


@pytest.fixture
async def project_with_questions(client, project_with_set):
    alice, pid, sid = project_with_set
    qs = []
    for text in ("q1", "q2", "q3"):
        r = await _add_question(client, alice, pid, sid, text)
        assert r.status_code == 201, r.text
        qs.append(r.json())
    return alice, pid, sid, qs


@pytest.fixture
async def run_ready(db_session, tmp_path, monkeypatch, fake_adapter, fake_cache):
    """A queued test_run job + run + 3 placeholder results over texts q1..q3,
    executable by the real worker through the fake seams. fail_on targets
    question TEXT, hence the terse texts."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    # db_session (unlike app) does not rebuild the shared engine, and its
    # pooled connections are bound to a previous test's event loop.
    await reset_engine()
    u = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
    db_session.add(u)
    await db_session.flush()
    p = Project(name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id, input_file_type="text")
    db_session.add(p)
    await db_session.flush()
    qs = QuestionSet(project_id=p.id, name="Smoke", created_by=u.id)
    db_session.add(qs)
    await db_session.flush()
    questions = [
        Question(set_id=qs.id, lineage_id=uuid.uuid4(), text=text, position=i, created_by=u.id)
        for i, text in enumerate(("q1", "q2", "q3"))
    ]
    db_session.add_all(questions)
    await db_session.flush()
    job = Job(
        project_id=p.id, type="test_run", method="local", argv=[], queued_by=u.id, status="queued"
    )
    db_session.add(job)
    await db_session.flush()
    run = models.TestRun(project_id=p.id, set_id=qs.id, job_id=job.id, method="local")
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        models.TestResult(run_id=run.id, question_id=q.id, position=i, question_text=q.text)
        for i, q in enumerate(questions)
    )
    # Plain JSONB columns without change tracking: assign, never mutate.
    job.params = {"run_id": str(run.id)}
    job.progress = {"done": 0, "total": len(questions)}
    await db_session.commit()
    root = _workspace(tmp_path / "ws" / str(p.id), settings=b"x: 1\n")
    yield SimpleNamespace(job_id=job.id, run_id=run.id, root=root)
    await reset_engine()
    get_settings.cache_clear()


async def test_manifest_is_frozen_at_enqueue(client, db_session, project_with_questions):
    """Editing, adding and archiving questions between enqueue and the worker
    claiming the job changes neither the executed questions nor progress.total."""
    alice, pid, sid, qs = project_with_questions  # three questions
    project, owner = await _owner_of(db_session, pid)
    run = await test_runs_service.enqueue_run(db_session, project, uuid.UUID(sid), "local", owner)

    # Mutations go through the real question API, so the manifest is proven
    # frozen against live traffic, not just against a stale read.
    assert (await _add_question(client, alice, pid, sid, "added later")).status_code == 201
    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{qs[0]['id']}",
        headers=alice,
        json={"text": "reworded later"},
    )
    assert r.status_code == 200, r.text
    assert (
        await client.delete(
            f"/api/projects/{pid}/question-sets/{sid}/questions/{qs[1]['id']}", headers=alice
        )
    ).status_code == 204

    rows = await _results(db_session, run.id)
    assert [row.question_text for row in rows] == [q["text"] for q in qs]

    job = await db_session.get(Job, run.job_id)
    assert job is not None
    assert job.progress == {"done": 0, "total": 3}
    assert job.params == {"run_id": str(run.id)}


async def test_placeholder_rows_are_honestly_null(db_session, project_with_questions):
    _alice, pid, sid, _qs = project_with_questions
    project, owner = await _owner_of(db_session, pid)
    run = await test_runs_service.enqueue_run(db_session, project, uuid.UUID(sid), "local", owner)
    rows = await _results(db_session, run.id)
    assert len(rows) == 3
    assert all(
        row.answer is None
        and row.citations is None
        and row.timings is None
        and row.error is None
        and row.completed_at is None
        for row in rows
    )


async def test_a_per_question_failure_does_not_fail_the_run(db_session, run_ready, fake_adapter):
    """test_results.error records it and the remaining questions still execute."""
    fake_adapter.fail_on("q2")
    res = await test_runs_service.execute_test_run(
        run_ready.job_id, run_ready.root, cancel_requested=lambda: False
    )

    assert res.status == "succeeded"
    rows = await _results(db_session, run_ready.run_id)
    assert [row.error is None for row in rows] == [True, False, True]
    assert all(row.completed_at is not None for row in rows)
    assert rows[0].answer == ANSWER and rows[1].answer is None and rows[2].answer == ANSWER


async def test_cancellation_keeps_the_results_already_produced(db_session, run_ready, fake_adapter):
    calls = {"n": 0}

    def cancel_after_one() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    res = await test_runs_service.execute_test_run(
        run_ready.job_id, run_ready.root, cancel_requested=cancel_after_one
    )
    assert res.status == "cancelled"

    rows = await _results(db_session, run_ready.run_id)
    assert rows[0].completed_at is not None
    assert rows[1].completed_at is None and rows[2].completed_at is None

    # A cancelled run still closes itself out; the remainder reads as not run.
    finished_at = (
        await db_session.execute(
            select(models.TestRun.finished_at).where(models.TestRun.id == run_ready.run_id)
        )
    ).scalar_one()
    assert finished_at is not None


async def test_progress_is_written_between_questions(db_session, run_ready, fake_adapter):
    seen = []
    original = jobs_repo.set_progress

    async def recording(session, job_id, done, total):
        seen.append((done, total))
        await original(session, job_id, done, total)

    jobs_repo.set_progress = recording
    try:
        await test_runs_service.execute_test_run(
            run_ready.job_id, run_ready.root, cancel_requested=lambda: False
        )
    finally:
        jobs_repo.set_progress = original
    assert seen == [(1, 3), (2, 3), (3, 3)]


async def test_one_configuration_per_run(db_session, run_ready, fake_adapter, monkeypatch):
    """Editing settings.yaml or .env between two questions of a running batch
    does not change which configuration the later questions use."""
    loads = {"n": 0}
    monkeypatch.setattr(query_service, "load_config", _counting(loads))

    await test_runs_service.execute_test_run(
        run_ready.job_id, run_ready.root, cancel_requested=lambda: False
    )
    assert loads["n"] == 1

    revision = (
        await db_session.execute(
            select(models.TestRun.workspace_config_revision).where(
                models.TestRun.id == run_ready.run_id
            )
        )
    ).scalar_one()
    assert revision


def test_config_revision_framing_distinguishes_the_ambiguous_cases(tmp_path):
    """Without a length delimiter, moving a line from the end of
    settings.yaml to the start of .env would produce the same digest, and a
    deleted .env would be indistinguishable from an empty one."""
    a = _workspace(tmp_path / "a", settings=b"x: 1\nFOO=bar\n", env=None)
    b = _workspace(tmp_path / "b", settings=b"x: 1\n", env=b"FOO=bar\n")
    assert test_runs_service.workspace_config_revision(
        a
    ) != test_runs_service.workspace_config_revision(b)

    missing = _workspace(tmp_path / "c", settings=b"x: 1\n", env=None)
    empty = _workspace(tmp_path / "d", settings=b"x: 1\n", env=b"")
    assert test_runs_service.workspace_config_revision(
        missing
    ) != test_runs_service.workspace_config_revision(empty)


async def test_config_revision_is_captured_with_the_load_not_after(
    db_session, run_ready, fake_adapter
):
    """Editing either file between the worker's config load and its
    provenance write cannot make workspace_config_revision describe a
    configuration the run did not use.

    read_settings and load_config(root) are two independent reads, so a
    naive capture can load configuration A and record the hash of
    configuration B.
    """
    settings_path = run_ready.root / "settings.yaml"
    before = test_runs_service.workspace_config_revision(run_ready.root)
    original = test_runs_service._load_run_config

    def edit_then_load(root):
        # Fires between the digest capture and the config load if - and only
        # if - the implementation separates them.
        settings_path.write_text(settings_path.read_text() + "\n# drifted\n")
        return original(root)

    test_runs_service._load_run_config = edit_then_load
    try:
        await test_runs_service.execute_test_run(
            run_ready.job_id, run_ready.root, cancel_requested=lambda: False
        )
    finally:
        test_runs_service._load_run_config = original

    after = test_runs_service.workspace_config_revision(run_ready.root)
    revision = (
        await db_session.execute(
            select(models.TestRun.workspace_config_revision).where(
                models.TestRun.id == run_ready.run_id
            )
        )
    ).scalar_one()
    assert after != before
    # The recorded revision must be one of the two, and specifically the one
    # captured atomically with the load under the lock - never a mixture.
    assert revision in (before, after)
    assert revision == before


async def test_test_runs_conflict_with_any_active_job_in_both_directions(
    db_session, project_with_questions
):
    """jobs_one_active_per_project has NO type predicate, so this is mutual -
    and the message must not imply only indexing can block."""
    _alice, pid, sid, _ = project_with_questions
    project, owner = await _owner_of(db_session, pid)
    await _queue_job(db_session, project, type_="index")

    with pytest.raises(JobConflictError):
        await test_runs_service.enqueue_run(db_session, project, uuid.UUID(sid), "local", owner)

    # The conflict leaves nothing behind: no run, no orphan placeholder rows.
    assert (await db_session.execute(select(models.TestRun))).scalars().all() == []
    assert (await db_session.execute(select(models.TestResult))).scalars().all() == []


async def test_an_active_test_run_blocks_an_index_job(db_session, project_with_questions):
    _alice, pid, sid, _ = project_with_questions
    project, owner = await _owner_of(db_session, pid)
    await test_runs_service.enqueue_run(db_session, project, uuid.UUID(sid), "local", owner)

    with pytest.raises(JobConflictError):
        await jobs_service.enqueue(db_session, project, "index", "standard", owner)


async def test_a_test_run_does_not_freeze_uploads(client, db_session, project_with_questions):
    """Regression guard for slice 1's freeze predicate: it covers index and
    update only — a test_run reads output/ and never touches input/."""
    alice, pid, sid, _ = project_with_questions
    project, owner = await _owner_of(db_session, pid)
    await test_runs_service.enqueue_run(db_session, project, uuid.UUID(sid), "local", owner)

    r = await client.post(
        f"/api/projects/{pid}/files", headers=alice, files={"file": ("x.md", b"x")}
    )
    assert r.status_code == 201


# --- The two LEGAL question-edit interleavings (spec 5.3/10) --------------
# Both sides of the lock exist only from this task on, which is why these
# live here and not in test_questions.py.


async def test_patch_blocked_by_a_run_enqueue_holding_the_lock(
    client, db_session, migrated_db, project_with_set
):
    """Barrier 1 of two LEGAL interleavings.

    Parking a PATCH after its reference check and letting POST commit
    describes the BROKEN design: under the lock protocol the check is
    already inside the lock, so POST would block. Here POST holds the lock
    with the manifest not yet committed; PATCH blocks, then on release
    re-checks, finds the reference, and forks a new lineage row.
    """
    alice, pid, sid = project_with_set
    q = (await _add_question(client, alice, pid, sid, "original")).json()
    project = await db_session.get(Project, uuid.UUID(pid))
    assert project is not None
    owner_id = project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    holding, release = asyncio.Event(), asyncio.Event()
    original_commit = test_runs_service._commit_manifest

    async def parking(session):
        holding.set()
        await release.wait()
        return await original_commit(session)

    test_runs_service._commit_manifest = parking
    try:
        async with factory() as s1, factory() as s2:
            owner = await s1.get(User, owner_id)
            enqueuer = asyncio.create_task(
                test_runs_service.enqueue_run(
                    s1, await s1.get(Project, project.id), uuid.UUID(sid), "local", owner
                )
            )
            await asyncio.wait_for(holding.wait(), timeout=5)

            editor = asyncio.create_task(
                questions_service.edit_question(
                    s2,
                    await s2.get(Project, project.id),
                    uuid.UUID(q["id"]),
                    "reworded",
                    owner_id,
                )
            )
            done, _ = await asyncio.wait({editor}, timeout=1.0)
            assert done == set(), "PATCH did not wait for the project lock"

            release.set()
            await enqueuer
            forked = await asyncio.wait_for(editor, timeout=5)
    finally:
        test_runs_service._commit_manifest = original_commit
        await engine.dispose()

    assert str(forked.id) != q["id"]
    assert str(forked.lineage_id) == q["lineage_id"]


async def test_run_enqueue_blocked_by_a_patch_holding_the_lock(
    client, db_session, migrated_db, project_with_set
):
    """Barrier 2: PATCH holds the lock with the edit not yet committed; POST
    blocks, then on release materializes the manifest with the NEW text."""
    alice, pid, sid = project_with_set
    q = (await _add_question(client, alice, pid, sid, "original")).json()
    project = await db_session.get(Project, uuid.UUID(pid))
    assert project is not None
    owner_id = project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    holding, release = asyncio.Event(), asyncio.Event()
    original_commit = questions_service._commit_edit

    async def parking(session):
        holding.set()
        await release.wait()
        return await original_commit(session)

    questions_service._commit_edit = parking
    try:
        async with factory() as s1, factory() as s2:
            owner = await s2.get(User, owner_id)
            editor = asyncio.create_task(
                questions_service.edit_question(
                    s1,
                    await s1.get(Project, project.id),
                    uuid.UUID(q["id"]),
                    "reworded",
                    owner_id,
                )
            )
            await asyncio.wait_for(holding.wait(), timeout=5)

            enqueuer = asyncio.create_task(
                test_runs_service.enqueue_run(
                    s2, await s2.get(Project, project.id), uuid.UUID(sid), "local", owner
                )
            )
            done, _ = await asyncio.wait({enqueuer}, timeout=1.0)
            assert done == set(), "POST did not wait for the project lock"

            release.set()
            await editor
            run = await asyncio.wait_for(enqueuer, timeout=5)
    finally:
        questions_service._commit_edit = original_commit
        await engine.dispose()

    # The manifest materializes with the text as of the enqueue's own read
    # under the lock (DB until Task 6's results API re-pins it).
    texts = (
        (
            await db_session.execute(
                select(models.TestResult.question_text)
                .where(models.TestResult.run_id == run.id)
                .order_by(models.TestResult.position)
            )
        )
        .scalars()
        .all()
    )
    assert texts == ["reworded"]


async def _run_times(db_session, run_id):
    return (
        await db_session.execute(
            select(models.TestRun.started_at, models.TestRun.finished_at).where(
                models.TestRun.id == run_id
            )
        )
    ).one()


async def test_a_failing_preamble_still_closes_the_run(db_session, run_ready, monkeypatch):
    """R2-12: started_at is committed before the preamble; when the preamble
    raises, the job fails and the run must not read as in progress forever."""
    from graphrag_ui.services import runner_loop

    async def _unindexed(*a, **kw):
        raise query_service.WorkspaceNotIndexedError("output/ is gone")

    monkeypatch.setattr(test_runs_service, "_prepare_query", _unindexed)
    async with get_session_factory()() as s:
        assert (await jobs_repo.claim_next(s, "w-test")).id == run_ready.job_id
    await runner_loop._execute(run_ready.job_id)

    job = await db_session.get(Job, run_ready.job_id)
    await db_session.refresh(job)
    assert job.status == "failed"
    started_at, finished_at = await _run_times(db_session, run_ready.run_id)
    assert started_at is not None and finished_at is not None


async def test_cancelling_a_queued_run_closes_it(db_session, run_ready):
    """R2-09 x R2-12: the queued cancel never reaches the worker, so the run
    is closed with the job — the workbench polls while finished_at is null."""
    async with get_session_factory()() as s:
        assert await jobs_repo.request_cancel(s, run_ready.job_id)
    started_at, finished_at = await _run_times(db_session, run_ready.run_id)
    assert started_at is None and finished_at is not None
