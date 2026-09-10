import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from graphrag_ui.adapters import models
from graphrag_ui.adapters.models import (
    Job,
    Project,
    Question,
    QuestionSet,
    User,
)
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER
from graphrag_ui.main import create_app
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.test_runs import rate_result
from tests.citation_fixtures import _run_index_to_failure, _run_index_to_success
from tests.test_files import (
    _alice,
    _make_project,
    _seed_baseline,
    _stub_parquet,
    _upload,
)
from tests.test_projects import _activate, _login, _setup_two_users


async def test_health():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_with_db(client):
    r = await client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok"
    assert body["graphrag"]  # version string cached at startup


# --- knowledge-base health aggregates (spec 7.5) ---


@pytest.fixture
async def alice_headers(client):
    return await _alice(client)


@pytest.fixture
async def indexed_project(client, db_session):
    """test_files's indexed_project rebuilt from its helpers (pytest 9
    forbids calling a fixture directly): a.md + b.md uploaded through the
    API, an epoch-1 baseline with available recovery, a stub
    documents.parquet. Yields (alice headers, pid)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    await _upload(client, alice, pid, "a.md", b"A")
    await _upload(client, alice, pid, "b.md", b"B")
    _stub_parquet(pid)
    await _seed_baseline(
        db_session,
        pid,
        {"a.md": hashlib.sha256(b"A").hexdigest(), "b.md": hashlib.sha256(b"B").hexdigest()},
        attributable=["a.md", "b.md"],
        recovery="available",
    )
    return alice, pid


@pytest.fixture
async def mixed_project(client, db_session):
    """Every index_state exactly once over one project: a.md indexed,
    b.md in the baseline but not attributable (skipped), c.md deleted
    (removed), d.md re-uploaded with new bytes (modified), e.md uploaded
    after the baseline (new). Yields (alice headers, pid)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    contents = {"a.md": b"A", "b.md": b"B", "c.md": b"C", "d.md": b"D"}
    for name, data in contents.items():
        await _upload(client, alice, pid, name, data)
    _stub_parquet(pid)
    await _seed_baseline(
        db_session,
        pid,
        {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        attributable=["a.md"],  # only a.md maps back to an indexed document title
        recovery="available",
    )
    assert (
        await client.delete(f"/api/projects/{pid}/files/c.md", headers=alice)
    ).status_code == 204
    await _upload(client, alice, pid, "d.md", b"D2")
    await _upload(client, alice, pid, "e.md", b"E")
    return alice, pid


@pytest.fixture
async def two_rated_runs(client, app, db_session):
    """Two finished runs over one 3-lineage set: one lineage went good ->
    poor between them, one stayed good, one was never rated. Explicit
    queued_at stamps order the runs (TestRun has no created_at, and id
    alone is random). Yields (alice headers, pid)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "Runs", "input_file_type": "text"}
        )
    ).json()["id"]
    project = await db_session.get(Project, uuid.UUID(pid))
    owner = await db_session.get(User, project.owner_id)

    qs = QuestionSet(project_id=project.id, name="H", created_by=owner.id)
    db_session.add(qs)
    await db_session.flush()
    questions = [
        Question(
            set_id=qs.id, lineage_id=uuid.uuid4(), text=f"q{i}", position=i, created_by=owner.id
        )
        for i in range(3)
    ]
    db_session.add_all(questions)
    await db_session.flush()

    for hours_ago, scores in ((2, ("good", "good", None)), (1, ("poor", "good", None))):
        job = Job(
            project_id=project.id,
            type="test_run",
            method="local",
            argv=[],
            queued_by=owner.id,
            status="succeeded",
            queued_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        )
        db_session.add(job)
        await db_session.flush()
        run = models.TestRun(project_id=project.id, set_id=qs.id, job_id=job.id, method="local")
        db_session.add(run)
        await db_session.flush()
        results = [
            models.TestResult(
                run_id=run.id,
                question_id=q.id,
                position=i,
                question_text=q.text,
                answer="Answer body",
                completed_at=datetime.now(UTC),
            )
            for i, q in enumerate(questions)
        ]
        db_session.add_all(results)
        await db_session.flush()
        for result, score in zip(results, scores):
            if score is not None:
                await rate_result(db_session, result, score, "", owner)
    return alice, pid


@pytest.fixture
async def three_projects_two_visible(client, app):
    """alice owns three projects; bob is a viewer on two of them (seeded
    the same way indexed_project_with_viewer seeds membership). Yields
    (bob headers, visible a, visible b, hidden)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pids = [
        (
            await client.post(
                "/api/projects", headers=alice, json={"name": n, "input_file_type": "text"}
            )
        ).json()["id"]
        for n in ("A", "B", "H")
    ]
    admin = await _login(client, "admin@test.local", "admin-new-1")
    bob_id = next(
        u["id"]
        for u in (await client.get("/api/admin/users", headers=admin)).json()
        if u["email"] == "bob@test.local"
    )
    for pid in pids[:2]:
        await client.put(
            f"/api/projects/{pid}/members/{bob_id}",
            headers=alice,
            json={"role_id": str(ROLE_ID_VIEWER)},
        )
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    return bob, pids[0], pids[1], pids[2]


async def test_health_counts_every_state_and_totals_the_union(client, mixed_project):
    alice, pid = mixed_project  # 1 new, 1 modified, 1 indexed, 1 skipped, 1 removed
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["files"] == {
        "new": 1,
        "modified": 1,
        "indexed": 1,
        "skipped": 1,
        "removed": 1,
        "total": 5,
    }


async def test_missing_output_under_an_existing_baseline_is_reported_as_a_fault(
    client, indexed_project
):
    """The combination carries a fault neither field shows alone."""
    alice, pid = indexed_project
    (ws_path(uuid.UUID(pid)) / "output" / "documents.parquet").unlink()

    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["has_baseline"] is True
    assert body["ingest_check"] == "unavailable_not_indexed"


async def test_artifacts_stale_after_a_failed_index(client, db_session, indexed_project):
    alice, pid = indexed_project
    project = await db_session.get(Project, uuid.UUID(pid))
    await _run_index_to_failure(db_session, project)

    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["artifacts_stale"] is True

    await _run_index_to_success(
        db_session, project, ["a.md", "b.md"], [("d1", "a.md"), ("d2", "b.md")]
    )
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["artifacts_stale"] is False


async def test_regressions_are_counted_server_side(client, two_rated_runs):
    alice, pid = two_rated_runs  # one lineage went good -> poor
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["latest_run"]["regressions"] == 1
    assert body["latest_run"]["ratings"] == {"good": 1, "fair": 0, "poor": 1, "unrated": 1}


async def test_batch_health_is_one_round_trip_filtered_to_visible_projects(
    client, three_projects_two_visible
):
    """Without it the list would issue one request per project."""
    viewer, visible_a, visible_b, hidden = three_projects_two_visible
    r = await client.get(
        f"/api/projects/health?ids={visible_a},{visible_b},{hidden}", headers=viewer
    )
    assert r.status_code == 200
    assert set(r.json()["projects"]) == {visible_a, visible_b}


async def test_batch_health_carries_skipped_and_ingest_check(client, mixed_project):
    alice, pid = mixed_project
    body = (await client.get(f"/api/projects/health?ids={pid}", headers=alice)).json()
    entry = body["projects"][pid]
    assert entry["files"]["skipped"] == 1
    assert entry["ingest_check"] == "available"
    assert "artifacts_stale" in entry and "has_baseline" in entry


async def test_batch_health_rejects_an_oversized_id_list(client, alice_headers):
    r = await client.get(
        "/api/projects/health?ids=" + ",".join([str(uuid.uuid4())] * 201),
        headers=alice_headers,
    )
    assert r.status_code == 422
