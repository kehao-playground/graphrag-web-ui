"""Run, result and rating endpoints (spec 5.3/8, plan Task 6).

The matrix's rows are question LINEAGES, not question rows: an edit keeps
one row while each cell carries the text actually asked, and a lineage a
run never asked renders as a null cell. Ratings are one current row per
result, project-shared and overwritten on re-rate (spec 5.3).

This file also re-pins through GET /api/test-runs/{rid}/results the
history-survival assertions that Tasks 3 and 5 could only make against
the DB (their module docstrings promise the API-level pin lives here):
a question edit forks the lineage, an archive soft-deletes, and the run's
question_text never changes meaning.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

# TestRun/TestResult are referenced through the module on purpose: pytest
# tries to collect any module-level name starting with "Test".
from graphrag_ui.adapters import models
from graphrag_ui.adapters.models import Job, Project
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import ROLE_ID_EDITOR, ROLE_ID_VIEWER
from graphrag_ui.domain.test_runs import count_regressions
from tests.test_projects import _activate, _setup_two_users
from tests.test_questions import _add_question


async def _member(client, admin, owner, pid, email, initial_pw, new_pw, role_id):
    """Activate an existing user as a project member with the given role."""
    users = (await client.get("/api/admin/users", headers=admin)).json()
    uid = next(u["id"] for u in users if u["email"] == email)
    r = await client.put(
        f"/api/projects/{pid}/members/{uid}",
        headers=owner,
        json={"role_id": str(role_id)},
    )
    assert r.status_code == 200, r.text
    return {"id": uid, "headers": await _activate(client, email, initial_pw, new_pw)}


async def _finish(db_session, job_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Pretend the worker ran a queued test_run to completion: job terminal,
    run timed, every placeholder completed with an answer."""
    job = await db_session.get(Job, job_id)
    job.status = "succeeded"
    run = await db_session.get(models.TestRun, run_id)
    run.started_at = run.finished_at = datetime.now(UTC)
    rows = (
        (
            await db_session.execute(
                select(models.TestResult).where(models.TestResult.run_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.answer = "answered"
        row.completed_at = datetime.now(UTC)
    await db_session.commit()


@pytest.fixture
async def base(client, app):
    """admin + activated alice owning a project with one (empty) set."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post(
        "/api/projects", headers=alice, json={"name": "Matrix", "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid}/question-sets", headers=alice, json={"name": "Smoke"}
    )
    assert r.status_code == 201, r.text
    return admin, alice, pid, r.json()["id"]


@pytest.fixture
async def two_runs_with_a_fork(client, app, db_session, base):
    """One lineage asked twice with an edit in between, plus a question the
    only the second run asked: run1 = [original], run2 = [reworded, added].
    Three question rows, TWO lineages — the fork pair shares a row."""
    _admin, alice, pid, sid = base
    q = (await _add_question(client, alice, pid, sid, "original")).json()

    r1 = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert r1.status_code == 201, r1.text
    run1 = r1.json()
    await _finish(db_session, uuid.UUID(run1["job_id"]), uuid.UUID(run1["id"]))

    forked = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice,
        json={"text": "reworded"},
    )
    assert forked.status_code == 200, forked.text
    assert forked.json()["lineage_id"] == q["lineage_id"]
    added = (await _add_question(client, alice, pid, sid, "added")).json()

    r2 = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert r2.status_code == 201, r2.text
    return alice, pid, q["lineage_id"], added["lineage_id"], r2.json()["id"]


@pytest.fixture
async def finished_run(client, app, db_session, base):
    """alice's project holds one finished run of one question; bob is an
    editor (edit_content) on it."""
    admin, alice, pid, sid = base
    bob = await _member(
        client, admin, alice, pid, "bob@test.local", "bob-pass-1234", "bob-pass-2", ROLE_ID_EDITOR
    )
    q = (await _add_question(client, alice, pid, sid, "How long is the warranty?")).json()
    project = await db_session.get(Project, uuid.UUID(pid))
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
    now = datetime.now(UTC)
    run = models.TestRun(
        project_id=project.id,
        set_id=uuid.UUID(sid),
        job_id=job.id,
        method="local",
        started_at=now,
        finished_at=now,
    )
    db_session.add(run)
    await db_session.flush()
    result = models.TestResult(
        run_id=run.id,
        question_id=uuid.UUID(q["id"]),
        position=0,
        question_text=q["text"],
        answer="42 months",
        completed_at=now,
    )
    db_session.add(result)
    await db_session.commit()
    return alice, bob, pid, str(result.id)


@pytest.fixture
async def viewer_on_a_finished_run(client, app, db_session, base):
    """A VIEWER on a project whose run has one (completed) result."""
    admin, alice, pid, sid = base
    viewer = await _member(
        client, admin, alice, pid, "bob@test.local", "bob-pass-1234", "bob-pass-2", ROLE_ID_VIEWER
    )
    q = (await _add_question(client, alice, pid, sid, "What changed?")).json()

    project = await db_session.get(Project, uuid.UUID(pid))
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
    run = models.TestRun(
        project_id=project.id, set_id=uuid.UUID(sid), job_id=job.id, method="local"
    )
    db_session.add(run)
    await db_session.flush()
    result = models.TestResult(
        run_id=run.id,
        question_id=uuid.UUID(q["id"]),
        position=0,
        question_text=q["text"],
        answer="nothing",
        completed_at=datetime.now(UTC),
    )
    db_session.add(result)
    await db_session.commit()
    ids = {"pid": pid, "sid": sid, "rid": str(run.id), "result_id": str(result.id)}
    return viewer["headers"], ids


@pytest.fixture
async def two_projects(client, app, db_session, base):
    """alice owns A; bob owns B with one enqueued run of one question."""
    _admin, alice, _pid_a, _sid_a = base
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-2")
    r = await client.post(
        "/api/projects", headers=bob, json={"name": "B", "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    pid_b = r.json()["id"]
    r = await client.post(
        f"/api/projects/{pid_b}/question-sets", headers=bob, json={"name": "B set"}
    )
    sid_b = r.json()["id"]
    r = await _add_question(client, bob, pid_b, sid_b, "secret of project B")
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/api/projects/{pid_b}/test-runs", headers=bob, json={"set_id": sid_b, "method": "local"}
    )
    assert r.status_code == 201, r.text
    run_b_id = uuid.UUID(r.json()["id"])
    result_b = (
        await db_session.execute(
            select(models.TestResult).where(models.TestResult.run_id == run_b_id)
        )
    ).scalar_one()
    return alice, str(run_b_id), str(result_b.id)


@pytest.fixture
async def project_with_jobs(client, app, db_session, base):
    """A project with one succeeded index job and one succeeded test_run job."""
    _admin, alice, pid, _sid = base
    project = await db_session.get(Project, uuid.UUID(pid))
    db_session.add_all(
        [
            Job(
                project_id=project.id,
                type="index",
                method="standard",
                argv=[],
                queued_by=project.owner_id,
                status="succeeded",
            ),
            Job(
                project_id=project.id,
                type="test_run",
                method="local",
                argv=[],
                queued_by=project.owner_id,
                status="succeeded",
            ),
        ]
    )
    await db_session.commit()
    return alice, pid


# --- the matrix (spec 5.3: rows are lineages) -----------------------------


async def test_matrix_rows_are_lineages_not_question_rows(client, two_runs_with_a_fork):
    alice, pid, lineage_id, added_lineage_id, _run2 = two_runs_with_a_fork
    body = (await client.get(f"/api/projects/{pid}/test-runs", headers=alice)).json()

    # Three question rows across the two runs, but the forked pair shares
    # one lineage: TWO rows.
    assert len(body["runs"]) == 2
    assert [row["lineage_id"] for row in body["rows"]] == [lineage_id, added_lineage_id]
    cells = body["rows"][0]["cells"]
    # One row, two cells, each carrying the text ACTUALLY asked.
    assert [c["question_text"] for c in cells] == ["original", "reworded"]


async def test_a_lineage_a_run_never_asked_is_a_null_cell(client, two_runs_with_a_fork):
    """The 'added' question exists only in run2: its run1 cell is null, and
    the completed/rating flags the matrix colors on read honestly."""
    alice, pid, lineage_id, _added_lineage_id, _run2 = two_runs_with_a_fork
    body = (await client.get(f"/api/projects/{pid}/test-runs", headers=alice)).json()

    by_lineage = {row["lineage_id"]: row for row in body["rows"]}
    added = by_lineage[_added_lineage_id]
    assert added["cells"][0] is None
    cell = by_lineage[lineage_id]["cells"][0]
    assert cell["completed"] is True and cell["rating"] is None


async def test_matrix_runs_window_is_overridable(client, two_runs_with_a_fork):
    alice, pid, _lineage_id, _added_lineage_id, _run2 = two_runs_with_a_fork
    body = (await client.get(f"/api/projects/{pid}/test-runs?runs=1", headers=alice)).json()

    # The MOST recent run only, so the forked lineage's single cell is the
    # reworded text.
    assert len(body["runs"]) == 1
    assert [c["question_text"] for c in body["rows"][0]["cells"]] == ["reworded"]


async def test_run_results_are_ordered_by_position(client, two_runs_with_a_fork):
    alice, _pid, _lineage_id, _added_lineage_id, run2 = two_runs_with_a_fork
    body = (await client.get(f"/api/test-runs/{run2}/results", headers=alice)).json()

    assert [r["question_text"] for r in body["results"]] == ["reworded", "added"]
    assert [r["position"] for r in body["results"]] == [0, 1]


# --- ratings (spec 5.3: one current rating per result) --------------------


async def test_rating_upsert_overwrites_and_records_the_rater(client, db_session, finished_run):
    alice, bob, _pid, result_id = finished_run
    assert (
        await client.put(
            f"/api/test-results/{result_id}/rating",
            headers=alice,
            json={"score": "fair", "note": ""},
        )
    ).status_code == 200
    r = await client.put(
        f"/api/test-results/{result_id}/rating",
        headers=bob["headers"],
        json={"score": "good", "note": "better after reindex"},
    )
    assert r.status_code == 200 and r.json()["score"] == "good"
    assert r.json()["rated_by"] == bob["id"]

    rows = (
        (
            await db_session.execute(
                select(models.ResultRating).where(
                    models.ResultRating.result_id == uuid.UUID(result_id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].note == "better after reindex"
    assert rows[0].rated_by == uuid.UUID(bob["id"])


async def test_rating_rejects_an_unknown_score(client, finished_run):
    alice, _, _, result_id = finished_run
    r = await client.put(
        f"/api/test-results/{result_id}/rating",
        headers=alice,
        json={"score": "excellent", "note": ""},
    )
    assert r.status_code == 422


# --- regression counting (pure; slice 3's overview consumes it) -----------


def test_count_regressions_counts_only_downward_moves():
    prev = {"l1": "good", "l2": "fair", "l3": "poor", "l4": "good"}
    curr = {"l1": "fair", "l2": "fair", "l3": "good", "l4": "poor"}
    assert count_regressions(prev, curr) == 2  # l1 and l4


def test_count_regressions_ignores_lineages_missing_on_either_side():
    assert count_regressions({"l1": "good"}, {}) == 0
    assert count_regressions({}, {"l1": "poor"}) == 0


# --- jobs list can exclude test runs server-side --------------------------


async def test_jobs_list_can_exclude_test_runs(client, project_with_jobs):
    alice, pid = project_with_jobs
    all_jobs = (await client.get(f"/api/projects/{pid}/jobs", headers=alice)).json()
    index_only = (await client.get(f"/api/projects/{pid}/jobs?type=index", headers=alice)).json()
    assert len(index_only) < len(all_jobs)
    assert all(j["type"] == "index" for j in index_only)


async def test_jobs_list_type_filter_rejects_unknown_types(client, project_with_jobs):
    alice, pid = project_with_jobs
    r = await client.get(f"/api/projects/{pid}/jobs?type=reindex", headers=alice)
    assert r.status_code == 422


# --- POST /test-runs and 404 error mapping ---------------------------------


async def test_post_run_on_an_empty_set_is_400(client, base):
    _admin, alice, pid, sid = base
    r = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert r.status_code == 400 and r.json()["code"] == "question_set_empty"


async def test_post_run_on_an_unknown_set_is_404(client, base):
    _admin, alice, pid, _sid = base
    r = await client.post(
        f"/api/projects/{pid}/test-runs",
        headers=alice,
        json={"set_id": str(uuid.uuid4()), "method": "local"},
    )
    assert r.status_code == 404 and r.json()["code"] == "question_set_not_found"


async def test_a_second_active_run_is_409_job_conflict(client, base):
    _admin, alice, pid, sid = base
    assert (await _add_question(client, alice, pid, sid, "q")).status_code == 201
    first = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert first.status_code == 201
    r = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"


async def test_reading_results_of_an_unknown_run_is_404(client, base):
    _admin, alice, _pid, _sid = base
    r = await client.get(f"/api/test-runs/{uuid.uuid4()}/results", headers=alice)
    assert r.status_code == 404 and r.json()["code"] == "test_run_not_found"


async def test_rating_an_unknown_result_is_404(client, base):
    _admin, alice, _pid, _sid = base
    r = await client.put(
        f"/api/test-results/{uuid.uuid4()}/rating",
        headers=alice,
        json={"score": "good", "note": ""},
    )
    assert r.status_code == 404 and r.json()["code"] == "test_result_not_found"


# --- authz (spec 8: view / edit_content / run_jobs) ------------------------


@pytest.mark.parametrize(
    ("method", "path", "body", "viewer_status"),
    [
        ("GET", "/api/projects/{pid}/question-sets", None, 200),
        ("POST", "/api/projects/{pid}/question-sets", {"name": "s"}, 403),
        ("GET", "/api/projects/{pid}/test-runs", None, 200),
        ("POST", "/api/projects/{pid}/test-runs", {"set_id": "{sid}", "method": "local"}, 403),
        ("GET", "/api/test-runs/{rid}/results", None, 200),
        ("PUT", "/api/test-results/{result_id}/rating", {"score": "good", "note": ""}, 403),
    ],
)
async def test_route_authz(client, viewer_on_a_finished_run, method, path, body, viewer_status):
    """Every new endpoint gated per spec 8: reading is project:view,
    curating content is project:edit_content, spending compute is
    project:run_jobs. A viewer may read runs and results and may neither
    enqueue one nor rate an answer."""
    viewer, ids = viewer_on_a_finished_run
    r = await client.request(
        method,
        path.format(**ids),
        headers=viewer,
        json=None
        if body is None
        else {k: v.format(**ids) if isinstance(v, str) else v for k, v in body.items()},
    )
    assert r.status_code == viewer_status


async def test_a_result_from_another_project_is_404_not_403(client, two_projects):
    """403 would confirm the row exists."""
    alice_a, _, result_id_b = two_projects
    r = await client.put(
        f"/api/test-results/{result_id_b}/rating",
        headers=alice_a,
        json={"score": "good", "note": ""},
    )
    assert r.status_code == 404
    assert r.json()["code"] != "forbidden"


async def test_reading_results_of_another_projects_run_is_404_not_403(client, two_projects):
    alice_a, run_id_b, _ = two_projects
    r = await client.get(f"/api/test-runs/{run_id_b}/results", headers=alice_a)
    assert r.status_code == 404
    assert r.json()["code"] == "test_run_not_found"


# --- history re-pins through the API (Tasks 3/5 asserted against the DB) --


@pytest.fixture
async def run_referencing_question(client, app, db_session, base):
    """A run already references q — the same shape test_questions.run_fixture
    builds; defined here (not imported) so this file stays self-contained."""
    _admin, alice, pid, sid = base
    r = await _add_question(client, alice, pid, sid, "How long is the warranty?")
    assert r.status_code == 201, r.text
    q = r.json()

    project = await db_session.get(Project, uuid.UUID(pid))
    job = Job(
        project_id=project.id,
        type="test_run",
        method="local",
        argv=[],
        queued_by=project.owner_id,
        status="queued",
    )
    db_session.add(job)
    await db_session.flush()
    run = models.TestRun(
        project_id=project.id, set_id=uuid.UUID(sid), job_id=job.id, method="local"
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


async def test_history_survives_a_question_edit(client, run_referencing_question):
    """API re-pin of test_questions' fork test: the fork keeps one lineage
    while the run's question_text stays as asked."""
    alice, pid, sid, run_id, q = run_referencing_question

    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice,
        json={"text": "Reworded"},
    )
    assert r.status_code == 200
    new = r.json()
    assert new["id"] != q["id"]
    assert new["lineage_id"] == q["lineage_id"]

    # The historic result keeps the text as asked.
    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    assert results["results"][0]["question_text"] == q["text"]


async def test_history_survives_a_question_archive(client, run_referencing_question):
    """API re-pin of test_questions' archive test."""
    alice, pid, sid, run_id, q = run_referencing_question
    r = await client.delete(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}", headers=alice
    )
    assert r.status_code == 204
    live = (
        await client.get(f"/api/projects/{pid}/question-sets/{sid}/questions", headers=alice)
    ).json()
    assert live["questions"] == []
    # History survives.
    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    assert results["results"][0]["question_text"] == q["text"]


async def test_history_survives_a_set_archive(client, run_referencing_question):
    """API re-pin of test_questions' set-archive test."""
    alice, pid, sid, run_id, _q = run_referencing_question
    assert (
        await client.delete(f"/api/projects/{pid}/question-sets/{sid}", headers=alice)
    ).status_code == 204

    sets = (await client.get(f"/api/projects/{pid}/question-sets", headers=alice)).json()
    assert [s["id"] for s in sets["sets"]] == []
    # Its runs stay readable.
    assert (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).status_code == 200
