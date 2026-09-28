"""Question sets and the immutability rule (spec 5.3).

A question never referenced by a run is edited in place - fixing a typo
before the first run must not fork history. Once a run references it, an
edit inserts a new row on the same lineage and archives the old one, so
the matrix keeps one row per lineage while each cell shows the text
actually asked.

History survival is asserted directly against the DB here because
GET /api/test-runs/{run_id}/results does not exist until Task 6, whose
tests re-pin these assertions through the API.
"""

import uuid

import pytest
from sqlalchemy import select

# TestRun/TestResult are referenced through the module on purpose: pytest
# tries to collect any module-level name starting with "Test".
from graphrag_ui.adapters import models
from graphrag_ui.adapters.models import AuditLog, Job, Project, Question
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from tests.test_projects import _activate, _setup_two_users


async def _add_question(client, headers, pid, sid, text):
    return await client.post(
        f"/api/projects/{pid}/question-sets/{sid}/questions",
        headers=headers,
        json={"text": text},
    )


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
async def run_fixture(client, db_session, project_with_set):
    """A run already references q: TestRun + one TestResult inserted DIRECTLY
    via db_session — the tables exist from Task 1 and enqueue_run does not
    exist until Task 5. What this task needs from a run is only that a
    test_results row references the question; how that row got there is
    Task 5's business."""
    alice, pid, sid = project_with_set
    r = await _add_question(client, alice, pid, sid, "How long is the warranty?")
    assert r.status_code == 201, r.text
    q = r.json()

    project = await db_session.get(Project, uuid.UUID(pid))
    job = Job(
        project_id=project.id,
        type="test_run",
        method="standard",
        argv=[],
        queued_by=project.owner_id,
        status="queued",
    )
    db_session.add(job)
    await db_session.flush()
    run = models.TestRun(
        project_id=project.id, set_id=uuid.UUID(sid), job_id=job.id, method="standard"
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        models.TestResult(
            run_id=run.id, question_id=uuid.UUID(q["id"]), position=0, question_text=q["text"]
        )
    )
    await db_session.commit()
    return alice, pid, sid, str(run.id), q


async def _history(db_session, run_id) -> list[str]:
    """The run's question_texts as the DB holds them (Task 6 re-pins via API)."""
    rows = await db_session.execute(
        select(models.TestResult.question_text).where(models.TestResult.run_id == uuid.UUID(run_id))
    )
    return list(rows.scalars())


async def test_editing_an_unreferenced_question_mutates_in_place(client, project_with_set):
    alice, pid, sid = project_with_set
    q = (await _add_question(client, alice, pid, sid, "How long is the warranty?")).json()

    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice,
        json={"text": "How long is the warranty period?"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == q["id"]
    assert r.json()["lineage_id"] == q["lineage_id"]

    live = (
        await client.get(f"/api/projects/{pid}/question-sets/{sid}/questions", headers=alice)
    ).json()
    assert [x["text"] for x in live["questions"]] == ["How long is the warranty period?"]


async def test_editing_a_referenced_question_forks_the_lineage(client, db_session, run_fixture):
    alice, pid, sid, run_id, q = run_fixture  # a run already references q

    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice,
        json={"text": "Reworded"},
    )
    assert r.status_code == 200
    new = r.json()
    assert new["id"] != q["id"]
    assert new["lineage_id"] == q["lineage_id"]

    old = await db_session.get(Question, uuid.UUID(q["id"]))
    assert old.archived_at is not None

    # The historic result keeps the text as asked (DB until Task 6's API).
    assert await _history(db_session, run_id) == [q["text"]]


async def test_deleting_a_question_archives_it(client, db_session, run_fixture):
    alice, pid, sid, run_id, q = run_fixture
    r = await client.delete(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}", headers=alice
    )
    assert r.status_code == 204
    live = (
        await client.get(f"/api/projects/{pid}/question-sets/{sid}/questions", headers=alice)
    ).json()
    assert live["questions"] == []
    # History survives (DB until Task 6's API).
    assert await _history(db_session, run_id) == [q["text"]]


async def test_a_set_referenced_by_a_run_archives_instead_of_cascading(
    client, db_session, run_fixture
):
    alice, pid, sid, run_id, _ = run_fixture
    assert (
        await client.delete(f"/api/projects/{pid}/question-sets/{sid}", headers=alice)
    ).status_code == 204

    sets = (await client.get(f"/api/projects/{pid}/question-sets", headers=alice)).json()
    assert [s["id"] for s in sets["sets"]] == []
    # Its runs stay readable (DB until Task 6's API).
    runs = await db_session.execute(
        select(models.TestRun.id).where(models.TestRun.id == uuid.UUID(run_id))
    )
    assert list(runs.scalars()) == [uuid.UUID(run_id)]


async def test_set_size_is_bounded(client, project_with_set):
    alice, pid, sid = project_with_set
    for i in range(200):
        assert (await _add_question(client, alice, pid, sid, f"q{i}")).status_code == 201
    r = await _add_question(client, alice, pid, sid, "one too many")
    assert r.status_code == 400 and r.json()["code"] == "question_set_too_large"


async def test_listing_a_foreign_sets_questions_is_404_not_a_leak(client, app, project_with_set):
    """live_questions is project-scoped: a user with project:view on their own
    project must not read another project's question text by supplying its
    set id under their own project path."""
    alice, pid, sid = project_with_set
    await _add_question(client, alice, pid, sid, "secret of another project")

    app.dependency_overrides[get_initializer] = FakeInitializer
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-2")
    r = await client.post(
        "/api/projects", headers=bob, json={"name": "Bob's own", "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    bob_pid = r.json()["id"]

    r = await client.get(f"/api/projects/{bob_pid}/question-sets/{sid}/questions", headers=bob)
    assert r.status_code == 404
    assert r.json()["code"] == "question_set_not_found"


async def test_renaming_a_set_changes_its_name_and_audits(client, db_session, project_with_set):
    """R3-23: spec 8 lists PATCH /question-sets/{sid}; a rename keeps the
    set's id, so its runs and the picker selection survive it."""
    alice, pid, sid = project_with_set
    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}", headers=alice, json={"name": "  Regression  "}
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == sid and r.json()["name"] == "Regression"
    sets = (await client.get(f"/api/projects/{pid}/question-sets", headers=alice)).json()
    assert [s["name"] for s in sets["sets"]] == ["Regression"]
    payload = (
        await db_session.execute(
            select(AuditLog.payload).where(
                AuditLog.action == "question_set.renamed", AuditLog.target_id == pid
            )
        )
    ).scalar_one()
    assert payload == {"set_id": sid, "old_name": "Smoke", "name": "Regression"}


async def test_renaming_an_archived_set_is_404(client, project_with_set):
    alice, pid, sid = project_with_set
    assert (
        await client.delete(f"/api/projects/{pid}/question-sets/{sid}", headers=alice)
    ).status_code == 204
    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}", headers=alice, json={"name": "Back"}
    )
    assert r.status_code == 404 and r.json()["code"] == "question_set_not_found"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_a_blank_set_name_is_rejected(client, project_with_set, method):
    """A whitespace-only name would render as an empty picker entry."""
    alice, pid, sid = project_with_set
    path = f"/api/projects/{pid}/question-sets" + (f"/{sid}" if method == "PATCH" else "")
    r = await client.request(method, path, headers=alice, json={"name": "   "})
    assert r.status_code == 422
