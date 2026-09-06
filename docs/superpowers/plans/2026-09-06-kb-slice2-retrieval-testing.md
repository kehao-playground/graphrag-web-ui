# Knowledge-Manager Slice ② — Retrieval Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a knowledge manager save a question set, re-run it in one action against the current index as a background job, and rate the answers — with a quality trend per question across runs that historical rows never change meaning inside.

**Architecture:** A batch run is a `test_run` job on the existing runner loop, so it is cancellable, survives the browser closing, is auditable, and is bounded by `MAX_CONCURRENT_JOBS` rather than by the interactive query limiter. `POST /test-runs` materializes the whole ordered question manifest as `test_results` placeholder rows in the same transaction as the job insert, under the project lock. Questions are immutable once referenced by a run: editing forks a new row on the same `lineage_id`. `services/query.py` grows a shared `_prepare_query` + `_execute_query` core that the batch and the non-streaming interactive path both call; SSE keeps `stream_query`.

**Tech Stack:** FastAPI + pydantic v2, SQLAlchemy 2 async + alembic + testcontainers (Postgres 16), React 19 + TS + antd 6 + vitest, openapi-typescript codegen.

**Spec:** `docs/superpowers/specs/2026-09-06-knowledge-manager-ux-design.md` — the spec travels with this plan; executors read both. Section references (§5.3, §7.2…) point at the spec.

**Depends on:** `docs/superpowers/plans/2026-09-06-kb-slice1-document-governance.md`. Slice ① Task 3 provides `services/project_lock.py` (`lock_project`, `assert_input_unfrozen`, `FREEZING_JOB_TYPES`), which this slice reuses. If slice ① has not landed, stop and say so rather than reimplementing the lock.

## Global Constraints

- **Layering** (AGENTS.md, CI-enforced): `domain/` pure; `services/` no FastAPI imports and no `HTTPException`, owns the transaction boundary; `adapters/` owns I/O and every graphrag touchpoint; `api/` owns routes/schemas/auth. The batch loop is **use-case and transaction orchestration**, so it lives in `services/test_runs.py`, not in an adapter. graphrag is reached only through the existing env-shielded `adapters/graphrag_search.py` — **no new graphrag import site**.
- **No new environment variables.** Bounds are domain constants: `MAX_QUESTIONS_PER_SET = 200`, `MAX_QUESTION_CHARS = 2000`, `MATRIX_DEFAULT_RUNS = 5`.
- **DB schema changes go through alembic only.** `tests/test_schema_drift.py` must be green at the end of every task.
- **Alembic head after slice ①** is slice ① Task 1's revision. Verify with `cd backend && uv run alembic heads` and set `down_revision` to whatever that prints — do not hard-code a guess.
- **`jobs_one_active_per_project` has no type predicate** (`migrations/versions/47b77c99bc8f_indexing_jobs.py:53-59` — a partial unique index on `project_id` where `status IN ('queued','running')`). Adding `test_run` therefore makes test runs and index jobs **mutually exclusive per project, in both directions**. This is kept deliberately: a test run whose index changes underneath it is meaningless, and `FrameCache`'s `(path, mtime, size)` validity key would otherwise reload parquet mid-write. The cost must appear in the product — `POST /test-runs` returns **409 `job_conflict`** while *any* job holds the project, including another test run, and the UI names which job is running rather than showing a dead button.
- **Batch execution does not pass through `services/rate_limit.py`.** That limiter guards interactive queries per `(user, project)` per hour; one 20-question batch would consume an entire bucket and could be rejected mid-set, leaving a partial run.
- **Everything the worker fills in is nullable by construction** — `test_results.answer`, `citations`, `timings`, `error`, `completed_at`, and `test_runs.started_at`/`finished_at`. A placeholder row must be insertable honestly; a non-null sentinel (empty string, `{}`) would be indistinguishable from a question that genuinely returned nothing.
- **Permission atoms**: reading is `project:view`; question sets, questions and ratings are `project:edit_content` (curating content); `POST /test-runs` is `project:run_jobs` — the same atom that gates indexing, for the same reason.
- **New error codes**: `job_conflict` already exists (its message widens from "an indexing job" to "a job"); add `question_set_not_found`, `question_not_found`, `question_set_too_large`, `test_run_not_found`. Every one needs a zh-TW **and** an en-US catalog entry.
- **Contract gate**: `openapi.json` + `frontend/src/api/types.generated.ts` regenerate in the SAME commit as any schema/route change. Generated files are never hand-edited — with one stated exception: `frontend/src/api/types.ts`'s `Citation` is hand-maintained because the SSE contract has no backend `response_model`.
- **Every task ends green**: `cd backend && uv run pytest -q -m "not slow"`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`. Frontend tasks additionally `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`.
- Comments/docstrings **English only** (CI-enforced); Conventional Commits in English; every new UI string in **both** locales in the same commit.

## File Structure

```
backend/src/graphrag_ui/
  domain/
    jobs.py                 # Task 2: JOB_TYPES += "test_run"; build_argv rejects it
    test_runs.py            # NEW (Task 6): regression counting, pure
  services/
    questions.py            # NEW (Task 3): sets, lineage-forking edits, project lock
    query.py                # Task 4: _prepare_query / _execute_query extraction
    test_runs.py            # NEW (Task 5): manifest at enqueue + the batch worker
    runner_loop.py          # Task 2: dispatch on job.type
  adapters/
    models.py               # Task 1: QuestionSet, Question, TestRun, TestResult,
                            # ResultRating, Job.params, Job.progress
    jobs_repo.py            # Task 5: progress writes
  api/
    questions_routes.py     # NEW (Task 3)
    test_runs_routes.py     # NEW (Task 6)
    jobs_routes.py          # Task 6: optional ?type= filter
backend/migrations/versions/
    <rev>_kb_question_sets_and_runs.py   # NEW (Task 1)
backend/tests/
    test_kb_runs_schema.py      # NEW (Task 1)
    test_domain_jobs.py         # Task 2: build_argv("test_run") raises
    test_runner_loop.py         # Task 2: dispatch
    test_questions.py           # NEW (Task 3): lineage immutability + the barrier
    test_query_core.py          # NEW (Task 4): limiter placement, identical bodies
    test_test_runs.py           # NEW (Task 5): manifest, isolation, cancellation
    test_test_runs_api.py       # NEW (Task 6): matrix, ratings, authz
frontend/src/
  components/
    tests/Workbench.tsx         # NEW (Task 7): the routed container
    tests/RatingMatrix.tsx      # NEW (Task 7)
    tests/AnswerView.tsx        # NEW (Task 7): shared by ad-hoc and batch
    tests/AdhocQuery.tsx        # NEW (Task 7): QueryPanel becomes this
    tests/ResultDrawer.tsx      # NEW (Task 8)
    tests/RunDiff.tsx           # NEW (Task 8)
    tests/sentenceDiff.ts       # NEW (Task 8): pure
    QueryPanel.tsx              # Task 7: reduced to a re-export shim, then deleted
  api/types.ts                  # Task 7: QuestionSet/TestRun aliases from codegen
  i18n/locales/{zh-TW,en-US}.ts # Tasks 7-8
```

---

### Task 1: Schema — question sets, runs, results, ratings

**Files:**
- Create: `backend/migrations/versions/<rev>_kb_question_sets_and_runs.py`
- Modify: `backend/src/graphrag_ui/adapters/models.py`
- Test: `backend/tests/test_kb_runs_schema.py` (new file)

**Interfaces:**
- Consumes: slice ① Task 1's revision as `down_revision`.
- Produces: ORM models `QuestionSet`, `Question`, `TestRun`, `TestResult`, `ResultRating`; `Job.params: Mapped[dict | None]` and `Job.progress: Mapped[dict | None]`.

**Why `question_text` is denormalized onto every result.** Storing only
`question_id` let an edited question retro-label an old answer, and a
deleted question forced a choice between blocking the delete and destroying
history. A historic run must be self-contained.

**Why `params` and not a set id.** A `test_run` job stores `{run_id}`. The
ordered question manifest lives in the `test_results` rows written by the
same transaction (Task 5), not in `params`: a set id would let the worker
read a set that has changed since enqueue. `argv` stays what it is — a
graphrag CLI argument vector — and is empty for job types that spawn no CLI.

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/test_kb_runs_schema.py`:

```python
"""Schema shape for slice 2 (spec 5.3/5.4).

Two facts a drift check cannot see: the one-result-per-(run, question)
unique key, and that every column the worker fills in is nullable. A
placeholder row must be insertable honestly - a non-null sentinel would be
indistinguishable from a question that genuinely returned nothing.
"""

import sqlalchemy as sa

from graphrag_ui.adapters.db import make_engine


async def _columns(migrated_db, table: str) -> dict[str, dict]:
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: sa.inspect(c).get_columns(table))
    finally:
        await engine.dispose()
    return {c["name"]: c for c in cols}


async def test_one_result_per_run_and_question(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            idx = await conn.run_sync(lambda c: sa.inspect(c).get_indexes("test_results"))
            uq = await conn.run_sync(
                lambda c: sa.inspect(c).get_unique_constraints("test_results")
            )
    finally:
        await engine.dispose()
    pairs = [list(i["column_names"]) for i in idx if i["unique"]] + [
        list(u["column_names"]) for u in uq
    ]
    assert ["run_id", "question_id"] in pairs, pairs


async def test_worker_written_columns_are_nullable(migrated_db):
    results = await _columns(migrated_db, "test_results")
    for name in ("answer", "citations", "timings", "error", "completed_at"):
        assert results[name]["nullable"] is True, name
    for name in ("run_id", "question_id", "position", "question_text"):
        assert results[name]["nullable"] is False, name

    runs = await _columns(migrated_db, "test_runs")
    for name in ("index_job_id", "workspace_config_revision", "started_at", "finished_at"):
        assert runs[name]["nullable"] is True, name


async def test_one_rating_per_result(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            idx = await conn.run_sync(lambda c: sa.inspect(c).get_indexes("result_ratings"))
            uq = await conn.run_sync(
                lambda c: sa.inspect(c).get_unique_constraints("result_ratings")
            )
    finally:
        await engine.dispose()
    cols = [list(i["column_names"]) for i in idx if i["unique"]] + [
        list(u["column_names"]) for u in uq
    ]
    assert ["result_id"] in cols, cols


async def test_jobs_gained_nullable_params_and_progress(migrated_db):
    jobs = await _columns(migrated_db, "jobs")
    assert jobs["params"]["nullable"] is True
    assert jobs["progress"]["nullable"] is True


async def test_questions_carry_a_lineage_and_an_archive_stamp(migrated_db):
    questions = await _columns(migrated_db, "questions")
    assert questions["lineage_id"]["nullable"] is False
    assert questions["archived_at"]["nullable"] is True
    sets = await _columns(migrated_db, "question_sets")
    assert sets["archived_at"]["nullable"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_kb_runs_schema.py -q`
Expected: FAIL — `NoSuchTableError: test_results`.

- [ ] **Step 3: Write the migration**

```python
def upgrade() -> None:
    op.create_table(
        "question_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Soft delete: a set referenced by a run is never hard-deleted. A
        # cascade would destroy exactly the history question_text exists to
        # protect (spec 5.3).
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_question_sets_project_id", "question_sets", ["project_id"])

    op.create_table(
        "questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "set_id", UUID(as_uuid=True),
            sa.ForeignKey("question_sets.id", ondelete="CASCADE"), nullable=False,
        ),
        # Stable across edits: the matrix's rows are lineages, not question
        # rows, so a question keeps one row while each cell shows the text
        # actually asked.
        sa.Column("lineage_id", UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_questions_set_id", "questions", ["set_id"])
    op.create_index("ix_questions_lineage_id", "questions", ["lineage_id"])

    op.create_table(
        "test_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "set_id", UUID(as_uuid=True), sa.ForeignKey("question_sets.id"), nullable=False
        ),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        # The last successful index/update at run start: it labels a matrix
        # column and is what makes runs comparable. Nullable for a project
        # queried before any index job row exists.
        sa.Column("index_job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        # Framed sha256 over settings.yaml AND .env, captured atomically with
        # the config load (spec 7.2).
        sa.Column("workspace_config_revision", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_test_runs_project_id", "test_runs", ["project_id"])

    op.create_table(
        "test_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        # The question AS ASKED, denormalized at enqueue: a historic run is
        # self-contained and an edit cannot retro-label an old answer.
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", JSONB, nullable=True),
        sa.Column("timings", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "question_id", name="uq_test_results_run_question"),
    )
    op.create_index("ix_test_results_run_id", "test_results", ["run_id"])

    op.create_table(
        "result_ratings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id", UUID(as_uuid=True),
            sa.ForeignKey("test_results.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("score", sa.String(8), nullable=False),  # good | fair | poor
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("rated_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "rated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.add_column("jobs", sa.Column("params", JSONB, nullable=True))
    op.add_column("jobs", sa.Column("progress", JSONB, nullable=True))
```

`downgrade()` drops them in reverse order.

- [ ] **Step 4: Mirror in `adapters/models.py`**

Add the five models and the two `Job` columns, following the existing
conventions (uuid PKs, `server_default=func.now()` timestamps). Widen
`Job.type`'s comment to `index|update|test_run`. `Job.argv` stays
non-nullable; a `test_run` inserts `[]`.

Add the docstring that records the shared-ratings decision on
`ResultRating`:

```python
class ResultRating(Base):
    """One CURRENT rating per result, project-shared rather than per-user.

    This is a team console: a maintainer must see the quality judgement
    their colleague recorded. Re-rating overwrites; who changed what is
    carried by the audit log (test.rated), not by row versioning.
    """
```

- [ ] **Step 5-7: Run, gate, commit**

```bash
cd backend && uv run pytest tests/test_kb_runs_schema.py tests/test_schema_drift.py -q
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/migrations backend/src/graphrag_ui/adapters/models.py backend/tests/test_kb_runs_schema.py
git commit -m "$(cat <<'EOF'
feat(tests): add question set, run, result and rating tables

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 2: A second kind of job on the existing runner

**Files:**
- Modify: `backend/src/graphrag_ui/domain/jobs.py`, `backend/src/graphrag_ui/services/runner_loop.py`
- Test: `backend/tests/test_domain_jobs.py` (extend), `backend/tests/test_runner_loop.py` (extend)

**Interfaces:**
- Consumes: `Job.type`, `Job.params` (Task 1).
- Produces: `domain/jobs.JOB_TYPES == ("index", "update", "test_run")`; `build_argv` raises `ValueError` for `test_run`; `services/runner_loop._execute` dispatches on `job.type` and both branches yield the existing `RunResult`.

**The smallest change that admits a second kind of work.** Claiming,
heartbeat, cancellation polling, terminal-state writing and `log_path_for`
logging stay shared and untouched. Duplicating the stale-worker
reconciliation for a second loop would be the expensive mistake here.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_domain_jobs.py`:

```python
def test_test_run_is_a_job_type_but_has_no_argv():
    """A test_run job spawns no CLI. Asking for an argv is a caller bug, not
    a silent empty list - an empty argv would reach create_subprocess_exec
    and fork bare `graphrag`."""
    assert "test_run" in JOB_TYPES
    with pytest.raises(ValueError, match="test_run"):
        build_argv("test_run", "standard", Path("/tmp/ws"))
```

Append to `backend/tests/test_runner_loop.py`:

```python
async def test_execute_dispatches_test_run_to_the_service_never_indexrunner(
    db_session, monkeypatch, tmp_path
):
    called = {"index": 0, "batch": 0}

    class ExplodingRunner:
        async def run(self, **kwargs):
            called["index"] += 1
            raise AssertionError("IndexRunner must not run a test_run job")

    async def fake_batch(job_id, root, *, cancel_requested):
        called["batch"] += 1
        return RunResult(status="succeeded", exit_code=None, error=None, stats=None)

    monkeypatch.setattr(runner_loop, "IndexRunner", ExplodingRunner)
    monkeypatch.setattr(runner_loop, "execute_test_run", fake_batch)

    job = await _running_job(db_session, type_="test_run", params={"run_id": str(uuid.uuid4())})
    await runner_loop._execute(job.id)

    assert called == {"index": 0, "batch": 1}
    await db_session.refresh(job)
    assert job.status == "succeeded"


async def test_execute_still_dispatches_index_to_indexrunner(db_session, monkeypatch, tmp_path):
    """Regression guard: the dispatch must not change the existing path."""
    called = {"index": 0}

    class RecordingRunner:
        async def run(self, **kwargs):
            called["index"] += 1
            return RunResult(status="succeeded", exit_code=0, error=None, stats=None)

    async def explode(*a, **kw):
        raise AssertionError("the batch service must not run an index job")

    monkeypatch.setattr(runner_loop, "IndexRunner", RecordingRunner)
    monkeypatch.setattr(runner_loop, "execute_test_run", explode)

    job = await _running_job(db_session, type_="index")
    await runner_loop._execute(job.id)
    assert called["index"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_domain_jobs.py tests/test_runner_loop.py -q`
Expected: FAIL — `build_argv` returns `["test_run", ...]` instead of raising; `execute_test_run` does not exist.

- [ ] **Step 3: Implement**

`domain/jobs.py`:

```python
JOB_TYPES = ("index", "update", "test_run")
CLI_JOB_TYPES = ("index", "update")


def build_argv(job_type: str, method: str, root: Path) -> list[str]:
    """graphrag CLI argv (without the executable) ...

    test_run is a valid job type with NO argv: it runs in-process through
    services/test_runs.py. Asking for one is a caller bug (spec 7.2).
    """
    if job_type not in CLI_JOB_TYPES:
        msg = f"job type has no CLI argv: {job_type}"
        raise ValueError(msg)
    if method not in JOB_METHODS:            # unchanged
        msg = f"unknown method: {method}"
        raise ValueError(msg)
    return [job_type, "--root", str(root), "--method", method]
```

`services/runner_loop.py::_execute` — replace the single
`IndexRunner().run(...)` call with a dispatch, keeping the surrounding
heartbeat/cancel/finally frame exactly as it is:

```python
        if job_type == "test_run":
            # Same RunResult contract, so claiming, heartbeat, cancellation
            # polling and terminal-state writing are shared untouched.
            res = await execute_test_run(job_id, root, cancel_requested=lambda: state["cancelled"])
        else:
            res = await IndexRunner().run(...)
```

`execute_test_run` is imported at module level so tests can
`monkeypatch.setattr(runner_loop, "execute_test_run", ...)` — the same seam
`IndexRunner` already uses. Task 5 supplies the real implementation; for
this task, add a stub in `services/test_runs.py` that raises
`NotImplementedError`, and have the test monkeypatch it.

The `prune_update_output` call at the end of `_execute` already guards on
`job_type == "update"`, so it needs no change.

- [ ] **Step 4-6: Run, gate, commit**

```bash
cd backend && uv run pytest tests/test_domain_jobs.py tests/test_runner_loop.py -q
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src backend/tests
git commit -m "$(cat <<'EOF'
feat(tests): dispatch a second job type on the existing runner loop

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 3: Question sets with stable identity

**Files:**
- Create: `backend/src/graphrag_ui/services/questions.py`, `backend/src/graphrag_ui/api/questions_routes.py`
- Modify: `backend/src/graphrag_ui/main.py` (register the router)
- Test: `backend/tests/test_questions.py` (new file)

**Interfaces:**
- Consumes: `QuestionSet`, `Question`, `TestResult` (Task 1); `lock_project` (slice ① Task 3).
- Produces: `services/questions.py`:
  - `async create_set(session, project, name, actor_id) -> QuestionSet`
  - `async list_sets(session, project) -> list[QuestionSet]` (archived excluded)
  - `async archive_set(session, project, set_id, actor_id) -> None`
  - `async add_question(session, project, set_id, text, actor_id) -> Question`
  - `async edit_question(session, project, question_id, text, actor_id) -> Question`
  - `async archive_question(session, project, question_id, actor_id) -> None`
  - `async live_questions(session, set_id) -> list[Question]` (ordered by `position`)
  - errors `QuestionSetNotFound`, `QuestionNotFound`, `QuestionSetTooLarge`
  - the module-level barrier seam `_commit_edit(session)`
  - Routes: `GET/POST /api/projects/{pid}/question-sets`, `DELETE /api/projects/{pid}/question-sets/{sid}`, `GET/POST /api/projects/{pid}/question-sets/{sid}/questions`, `PATCH/DELETE /api/projects/{pid}/question-sets/{sid}/questions/{qid}`.

**Immutability is a check-then-act, so it takes the lock.** A `PATCH` could
find a question unreferenced, pause, let `POST /test-runs` commit a manifest
referencing it, and then edit it in place — the exact violation the rule
exists to prevent. `PATCH` and `DELETE` on a question or a set take
`SELECT projects … FOR UPDATE`, re-check references **inside** the lock, and
commit there.

- [ ] **Step 1: Write the failing tests, barriers included**

Create `backend/tests/test_questions.py`:

```python
"""Question sets and the immutability rule (spec 5.3).

A question never referenced by a run is edited in place - fixing a typo
before the first run must not fork history. Once a run references it, an
edit inserts a new row on the same lineage and archives the old one, so
the matrix keeps one row per lineage while each cell shows the text
actually asked.
"""


async def test_editing_an_unreferenced_question_mutates_in_place(client, project_with_set):
    alice, pid, sid = project_with_set
    q = (await _add_question(client, alice, pid, sid, "How long is the warranty?")).json()

    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice, json={"text": "How long is the warranty period?"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == q["id"]
    assert r.json()["lineage_id"] == q["lineage_id"]

    live = (await client.get(f"/api/projects/{pid}/question-sets/{sid}/questions", headers=alice)).json()
    assert [x["text"] for x in live["questions"]] == ["How long is the warranty period?"]


async def test_editing_a_referenced_question_forks_the_lineage(client, db_session, run_fixture):
    alice, pid, sid, run_id, q = run_fixture  # a run already references q

    r = await client.patch(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}",
        headers=alice, json={"text": "Reworded"},
    )
    assert r.status_code == 200
    new = r.json()
    assert new["id"] != q["id"]
    assert new["lineage_id"] == q["lineage_id"]

    old = await db_session.get(Question, uuid.UUID(q["id"]))
    assert old.archived_at is not None

    # The historic result keeps the text as asked.
    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    assert results["results"][0]["question_text"] == q["text"]


async def test_deleting_a_question_archives_it(client, run_fixture):
    alice, pid, sid, run_id, q = run_fixture
    r = await client.delete(
        f"/api/projects/{pid}/question-sets/{sid}/questions/{q['id']}", headers=alice
    )
    assert r.status_code == 204
    live = (await client.get(f"/api/projects/{pid}/question-sets/{sid}/questions", headers=alice)).json()
    assert live["questions"] == []
    # History survives.
    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    assert results["results"][0]["question_text"] == q["text"]


async def test_a_set_referenced_by_a_run_archives_instead_of_cascading(client, run_fixture):
    alice, pid, sid, run_id, _ = run_fixture
    assert (await client.delete(f"/api/projects/{pid}/question-sets/{sid}", headers=alice)).status_code == 204

    sets = (await client.get(f"/api/projects/{pid}/question-sets", headers=alice)).json()
    assert [s["id"] for s in sets["sets"]] == []
    # Its runs stay readable.
    assert (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).status_code == 200


async def test_set_size_is_bounded(client, project_with_set):
    alice, pid, sid = project_with_set
    for i in range(200):
        assert (await _add_question(client, alice, pid, sid, f"q{i}")).status_code == 201
    r = await _add_question(client, alice, pid, sid, "one too many")
    assert r.status_code == 400 and r.json()["code"] == "question_set_too_large"
```

> `run_fixture` inserts a `TestRun` plus one `TestResult` **directly** —
> the tables exist from Task 1 and `enqueue_run` does not exist until Task
> 5. What this task needs from a run is only that a `test_results` row
> references the question; how that row got there is Task 5's business. The
> two lock-barrier interleavings between `PATCH` and `POST /test-runs` are
> therefore written in Task 5, where both sides exist.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_questions.py -q`
Expected: FAIL — 404 on `/question-sets`.

- [ ] **Step 3: Implement `services/questions.py`**

```python
async def edit_question(session, project, question_id, text, actor_id) -> Question:
    """Edit in place, or fork the lineage when a run already references it.

    The reference check is a check-then-act, so it runs INSIDE the project
    lock: without it a PATCH could find the question unreferenced, pause,
    let POST /test-runs commit a manifest referencing it, and then edit in
    place - the exact violation this rule exists to prevent (spec 5.3).
    """
    await lock_project(session, project.id)
    q = await _question_or_raise(session, project.id, question_id)
    referenced = await _is_referenced(session, q.id)
    if not referenced:
        q.text = text
        await audit(session, actor_id, "question.updated", "project", str(project.id),
                    {"question_id": str(q.id)})
        await _commit_edit(session)
        return q
    q.archived_at = func.now()
    forked = Question(
        set_id=q.set_id, lineage_id=q.lineage_id, text=text, position=q.position,
        created_by=actor_id,
    )
    session.add(forked)
    await audit(session, actor_id, "question.forked", "project", str(project.id),
                {"lineage_id": str(q.lineage_id)})
    await _commit_edit(session)
    return forked
```

`_commit_edit(session)` is a module-level one-liner (`await session.commit()`)
that exists purely as the barrier seam Step 1's second test parks in.
`_is_referenced` is `SELECT 1 FROM test_results WHERE question_id = :id LIMIT 1`.
`archive_set` checks for referencing runs the same way, under the same lock;
with no runs it still archives rather than deleting, so there is one
behavior to reason about.

- [ ] **Step 4: Add the routes**

Bodies use `extra="forbid"`; `QuestionIn.text` is
`Field(min_length=1, max_length=MAX_QUESTION_CHARS)`. Atoms: `GET` is
`project:view`, every mutation is `project:edit_content`.

- [ ] **Step 5-7: Run, regenerate, gate, commit**

```bash
cd backend && uv run pytest tests/test_questions.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(tests): add question sets with lineage-stable question identity

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 4: The shared query core

**Files:**
- Modify: `backend/src/graphrag_ui/services/query.py`
- Test: `backend/tests/test_query_core.py` (new), `backend/tests/test_query_api.py` and `test_query_stream_sse.py` (must stay green untouched)

**Interfaces:**
- Consumes: `GraphragSearchAdapter`, `load_config`, `get_frame_cache`, `build_citations` (all existing).
- Produces:
  - `@dataclass(frozen=True) Prepared` with fields `root: Path`, `config: Any`, `frames: dict[str, pd.DataFrame]`, `frames_ms: float`.
  - `async _prepare_query(project, method, *, config=None) -> Prepared` — config load (or reuse a caller-supplied one), frames, `frames_ms`. **No limiter, no user.**
  - `async _execute_query(prepared, method, query, response_type) -> dict` — search → citations → timings. **No limiter.**
  - `run_query` and `stream_query` keep their exact signatures and behavior.

**A shared core is required, but it is not one path for every answer.**
There are two answer-producing implementations and they cannot merge:
`run_query` → `GraphragSearchAdapter.search` returns a body plus
`context_data`, which is what its citations join against; `stream_query` →
`GraphragSearchAdapter.stream` is an async generator, and streaming returns
no `context_data`, so its citations join against the very frames handed to
the adapter. What they genuinely share is the preamble and the tail, and
that is what gets extracted. Do not "simplify" `stream_query` into
`_execute_query` — the SSE route is the path the UI actually uses, and it
would stop streaming.

- [ ] **Step 1: Write the failing core tests**

Create `backend/tests/test_query_core.py`:

```python
"""The shared query core (spec 7.2): the preamble and the tail are shared;
the two searches are not.

The limiter placement is the load-bearing assertion. If _execute_query
carried the limiter, a 20-question batch would consume an entire
interactive bucket and could be rejected mid-set, leaving a partial run -
which is the whole reason batch execution is a job.
"""

import pytest

from graphrag_ui.services import query as query_service
from graphrag_ui.services.rate_limit import QueryRateLimitedError, get_rate_limiter


async def test_execute_query_applies_no_limiter(project, fake_adapter, monkeypatch):
    limiter = get_rate_limiter()
    for _ in range(limiter.limit):
        limiter.check("u1", str(project.id))

    prepared = await query_service._prepare_query(project, "local")
    body = await query_service._execute_query(prepared, "local", "q", None)
    assert body["answer"]


async def test_run_query_still_applies_the_limiter(project, user, fake_adapter):
    limiter = get_rate_limiter()
    for _ in range(limiter.limit):
        limiter.check(str(user.id), str(project.id))

    with pytest.raises(QueryRateLimitedError):
        await query_service.run_query(project, user, "local", "q")


async def test_run_query_and_the_core_produce_identical_bodies(project, user, fake_adapter):
    direct = await query_service.run_query(project, user, "local", "q")
    prepared = await query_service._prepare_query(project, "local")
    core = await query_service._execute_query(prepared, "local", "q", None)
    assert direct["answer"] == core["answer"]
    assert direct["citations"] == core["citations"]
    assert set(direct["timings"]) == set(core["timings"])


async def test_prepare_query_reuses_a_caller_supplied_config(project, fake_adapter, monkeypatch):
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


async def test_streaming_still_streams(project, user, fake_adapter):
    """Regression guard: stream_query must remain an async generator with
    chunk -> citations -> done, not a wrapper around _execute_query."""
    kinds = [kind async for kind, _ in query_service.stream_query(project, user, "local", "q")]
    assert kinds[0] == "chunk"
    assert kinds[-2:] == ["citations", "done"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_query_core.py -q`
Expected: FAIL — `AttributeError: module has no attribute '_prepare_query'`.

- [ ] **Step 3: Extract the core**

```python
@dataclass(frozen=True)
class Prepared:
    root: Path
    config: Any
    frames: dict[str, pd.DataFrame]
    frames_ms: float


async def _prepare_query(project: Project, method: str, *, config: Any = None) -> Prepared:
    """Config load (or reuse a caller-supplied one) -> frames -> frames_ms.

    No limiter and no user on purpose: this is the part the batch service
    shares with the interactive paths, and the batch is bounded by
    MAX_CONCURRENT_JOBS instead (spec 7.3). The `config` parameter exists
    so services/test_runs.py can load configuration once at worker start
    and reuse it for every question.
    """


async def _execute_query(prepared: Prepared, method: str, query: str, response_type: str | None) -> dict:
    """search -> citations -> timings. Non-streaming only: the adapter's
    search returns context_data, which is what these citations join
    against. Streaming has no context_data and keeps its own tail in
    stream_query."""
```

`run_query` becomes limiter + `_prepare_query` + `_execute_query`, preserving
its `total_ms` measured from its own entry. `stream_query` becomes limiter +
`_prepare_query` + its existing stream loop and tail, unchanged in behavior.
The error mapping (`QueryError("config", …)` / `QueryError("search", …)`,
`WorkspaceNotIndexedError` re-raised as-is) moves into `_prepare_query` and
`_execute_query` byte-for-byte, so route behavior is untouched.

- [ ] **Step 4: Run every query suite**

Run: `cd backend && uv run pytest tests/test_query_core.py tests/test_query_api.py tests/test_query_stream_sse.py tests/test_rate_limit.py -q`
Expected: PASS. The existing suites must pass **without edits** — if one
needed changing, the extraction changed behavior.

- [ ] **Step 5: Gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src/graphrag_ui/services/query.py backend/tests/test_query_core.py
git commit -m "$(cat <<'EOF'
refactor(query): share the query preamble and tail without merging searches

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 5: The batch run — manifest at enqueue, worker, progress

**Files:**
- Create: `backend/src/graphrag_ui/services/test_runs.py` (replacing Task 2's stub)
- Modify: `backend/src/graphrag_ui/adapters/jobs_repo.py`
- Test: `backend/tests/test_test_runs.py` (new file)

**Interfaces:**
- Consumes: `_prepare_query`, `_execute_query` (Task 4); `lock_project` (slice ①); `Question`, `TestRun`, `TestResult` (Task 1); `jobs_repo.insert_job`.
- Produces:
  - `async enqueue_run(session, project, set_id, method, actor) -> TestRun` — writes the run row, the job row and every `test_results` placeholder in **one** transaction under the project lock.
  - `async execute_test_run(job_id, root, *, cancel_requested: Callable[[], bool]) -> RunResult` — the worker entry point `runner_loop` dispatches to.
  - `def workspace_config_revision(root: Path) -> str` — the framed digest.
  - `async jobs_repo.set_progress(session, job_id, done, total) -> None`.
  - Two module-level barrier seams the tests bind to: `_commit_manifest(session)` and `_load_run_config(root)`.

**The manifest is materialized at enqueue, not at execution.** Between
`POST /test-runs` and the worker claiming the job, the set can be edited —
questions added, reworded, archived. If the worker read the set when it
started, the launch dialog's question count, `progress.total` and what
actually ran could all disagree; reading per question would mix versions
inside one run. The placeholder rows **are** the manifest: ordered,
immutable, and already the thing the worker fills in. `progress.total` is
their count. A cancelled run simply leaves the remainder with
`completed_at = NULL`, which the matrix renders as *not run* rather than as
an empty answer.

**Recording the configuration honestly takes more than the settings hash.**
`read_settings` (`settings.py:54`) hashes `settings.yaml` bytes only, but
the effective configuration also depends on `.env` — which is the very
reason §6.3 freezes `.env` during indexing. And `read_settings` and
`load_config(root)` are two independent reads, so a naive capture can load
configuration A and record the hash of configuration B. The worker takes the
project row lock **briefly** at start, reads both files, computes the
digest, calls `load_config`, and releases. The lock is not held for the run.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_test_runs.py`:

```python
"""Batch test runs (spec 5.3/7.2/7.3)."""


async def test_manifest_is_frozen_at_enqueue(client, db_session, project_with_questions):
    """Editing, adding and archiving questions between POST /test-runs and
    the worker claiming the job changes neither the executed questions nor
    progress.total."""
    alice, pid, sid, qs = project_with_questions  # three questions
    run = (await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )).json()

    await client.post(f"/api/projects/{pid}/question-sets/{sid}/questions",
                      headers=alice, json={"text": "added later"})
    await client.patch(f"/api/projects/{pid}/question-sets/{sid}/questions/{qs[0]['id']}",
                       headers=alice, json={"text": "reworded later"})
    await client.delete(f"/api/projects/{pid}/question-sets/{sid}/questions/{qs[1]['id']}",
                        headers=alice)

    results = (await client.get(f"/api/test-runs/{run['id']}/results", headers=alice)).json()
    assert [r["question_text"] for r in results["results"]] == [q["text"] for q in qs]

    job = await db_session.get(Job, uuid.UUID(run["job_id"]))
    assert job.progress["total"] == 3
    assert job.params == {"run_id": run["id"]}


async def test_placeholder_rows_are_honestly_null(client, db_session, project_with_questions):
    alice, pid, sid, qs = project_with_questions
    run = (await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )).json()
    rows = (await db_session.execute(
        select(TestResult).where(TestResult.run_id == uuid.UUID(run["id"]))
    )).scalars().all()
    assert all(
        r.answer is None and r.citations is None and r.timings is None
        and r.error is None and r.completed_at is None
        for r in rows
    )


async def test_a_per_question_failure_does_not_fail_the_run(db_session, run_ready, fake_adapter):
    """test_results.error records it, the matrix marks that cell errored,
    and the remaining questions still execute."""
    fake_adapter.fail_on("q2")
    res = await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)

    assert res.status == "succeeded"
    rows = await _results(db_session, run_ready.run_id)
    assert [r.error is None for r in rows] == [True, False, True]
    assert all(r.completed_at is not None for r in rows)


async def test_cancellation_keeps_the_results_already_produced(db_session, run_ready, fake_adapter):
    calls = {"n": 0}

    def cancel_after_one() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    res = await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=cancel_after_one)
    assert res.status == "cancelled"

    rows = await _results(db_session, run_ready.run_id)
    assert rows[0].completed_at is not None
    assert rows[1].completed_at is None and rows[2].completed_at is None


async def test_progress_is_written_between_questions(db_session, run_ready, fake_adapter):
    seen = []
    original = jobs_repo.set_progress

    async def recording(session, job_id, done, total):
        seen.append((done, total))
        await original(session, job_id, done, total)

    jobs_repo.set_progress = recording
    try:
        await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
    finally:
        jobs_repo.set_progress = original
    assert seen == [(1, 3), (2, 3), (3, 3)]


async def test_one_configuration_per_run(db_session, run_ready, fake_adapter, monkeypatch):
    """Editing settings.yaml or .env between two questions of a running batch
    does not change which configuration the later questions use."""
    loads = {"n": 0}
    monkeypatch.setattr(query_service, "load_config", _counting(loads))

    await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
    assert loads["n"] == 1

    run = await db_session.get(TestRun, run_ready.run_id)
    assert run.workspace_config_revision


def test_config_revision_framing_distinguishes_the_ambiguous_cases(tmp_path):
    """Without a length delimiter, moving a line from the end of
    settings.yaml to the start of .env would produce the same digest, and a
    deleted .env would be indistinguishable from an empty one."""
    a = _workspace(tmp_path / "a", settings=b"x: 1\nFOO=bar\n", env=None)
    b = _workspace(tmp_path / "b", settings=b"x: 1\n", env=b"FOO=bar\n")
    assert workspace_config_revision(a) != workspace_config_revision(b)

    missing = _workspace(tmp_path / "c", settings=b"x: 1\n", env=None)
    empty = _workspace(tmp_path / "d", settings=b"x: 1\n", env=b"")
    assert workspace_config_revision(missing) != workspace_config_revision(empty)


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
    before = workspace_config_revision(run_ready.root)
    original = test_runs_service._load_run_config

    def edit_then_load(root):
        # Fires between the digest capture and the config load if - and only
        # if - the implementation separates them.
        settings_path.write_text(settings_path.read_text() + "\n# drifted\n")
        return original(root)

    setattr(test_runs_service, "_load_run_config", edit_then_load)
    try:
        await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
    finally:
        setattr(test_runs_service, "_load_run_config", original)

    run = await db_session.get(TestRun, run_ready.run_id)
    after = workspace_config_revision(run_ready.root)
    assert after != before
    # The recorded revision must be one of the two, and specifically the one
    # the run actually loaded under the lock - never a mixture.
    assert run.workspace_config_revision in (before, after)
    assert run.workspace_config_revision == before


async def test_test_runs_conflict_with_any_active_job_in_both_directions(
    client, db_session, project_with_questions
):
    """jobs_one_active_per_project has NO type predicate, so this is
    mutual - and the message must not imply only indexing can block."""
    alice, pid, sid, _ = project_with_questions
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_job(db_session, project, type_="index")

    r = await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"


async def test_an_active_test_run_blocks_an_index_job(client, db_session, project_with_questions):
    alice, pid, sid, _ = project_with_questions
    await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    r = await client.post(f"/api/projects/{pid}/jobs", headers=alice,
                          json={"type": "index", "method": "standard"})
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"


async def test_a_test_run_does_not_freeze_uploads(client, db_session, project_with_questions):
    """Regression guard for slice 1's freeze predicate."""
    alice, pid, sid, _ = project_with_questions
    await client.post(
        f"/api/projects/{pid}/test-runs", headers=alice, json={"set_id": sid, "method": "local"}
    )
    r = await client.post(f"/api/projects/{pid}/files", headers=alice,
                          files={"file": ("x.md", b"x")})
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
    owner_id = project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    holding, release = asyncio.Event(), asyncio.Event()
    original_commit = test_runs_service._commit_manifest

    async def parking(session):
        holding.set()
        await release.wait()
        return await original_commit(session)

    setattr(test_runs_service, "_commit_manifest", parking)
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
                    s2, await s2.get(Project, project.id), uuid.UUID(q["id"]),
                    "reworded", owner_id,
                )
            )
            done, _ = await asyncio.wait({editor}, timeout=1.0)
            assert done == set(), "PATCH did not wait for the project lock"

            release.set()
            await enqueuer
            forked = await asyncio.wait_for(editor, timeout=5)
    finally:
        setattr(test_runs_service, "_commit_manifest", original_commit)
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
    owner_id = project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    holding, release = asyncio.Event(), asyncio.Event()
    original_commit = questions_service._commit_edit

    async def parking(session):
        holding.set()
        await release.wait()
        return await original_commit(session)

    setattr(questions_service, "_commit_edit", parking)
    try:
        async with factory() as s1, factory() as s2:
            owner = await s2.get(User, owner_id)
            editor = asyncio.create_task(
                questions_service.edit_question(
                    s1, await s1.get(Project, project.id), uuid.UUID(q["id"]),
                    "reworded", owner_id,
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
        setattr(questions_service, "_commit_edit", original_commit)
        await engine.dispose()

    results = (await client.get(f"/api/test-runs/{run.id}/results", headers=alice)).json()
    assert results["results"][0]["question_text"] == "reworded"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_test_runs.py -q`
Expected: FAIL — `NotImplementedError` from Task 2's stub.

- [ ] **Step 3: Implement `enqueue_run`**

```python
async def enqueue_run(session, project, set_id, method, actor) -> TestRun:
    """Run row + job row + one placeholder result per question, ONE
    transaction under the project lock (spec 5.3).

    The placeholder rows ARE the manifest. jobs.params carries only
    {run_id}: a set id would let the worker read a set that has changed
    since enqueue.
    """
    await lock_project(session, project.id)
    questions = await live_questions(session, set_id)
    if not questions:
        raise EmptyQuestionSetError(str(set_id))
    index_job_id = await _last_successful_index_job(session, project.id)
    try:
        job = await jobs_repo.insert_job(
            session, project_id=project.id, type="test_run", method=method,
            argv=[], queued_by=actor.id,
        )
        run = TestRun(
            project_id=project.id, set_id=set_id, job_id=job.id,
            index_job_id=index_job_id, method=method,
        )
        session.add(run)
        await session.flush()
        session.add_all([
            TestResult(run_id=run.id, question_id=q.id, position=i, question_text=q.text)
            for i, q in enumerate(questions)
        ])
        job.params = {"run_id": str(run.id)}
        job.progress = {"done": 0, "total": len(questions)}
        await audit(session, actor.id, "test_run.enqueued", "project", str(project.id),
                    {"run_id": str(run.id), "set_id": str(set_id), "questions": len(questions)})
        await _commit_manifest(session)
    except IntegrityError:
        # jobs_one_active_per_project fired: an index, an update, or another
        # test run holds the project. Never check-then-insert.
        await session.rollback()
        raise JobConflictError(str(project.id)) from None
    return run
```

`_commit_manifest(session)` is the module-level barrier seam Task 3's first
test parks in.

- [ ] **Step 4: Implement `execute_test_run` and the digest**

```python
def workspace_config_revision(root: Path) -> str:
    """sha256 over a canonical FRAMING of settings.yaml and .env, in that
    fixed order: name || b"\\n" || length || b"\\n" || bytes, with a missing
    file length -1 and an empty file length 0.

    Not a concatenation: without a length delimiter, moving a line from the
    end of settings.yaml to the start of .env would produce the same digest,
    and a deleted .env would be indistinguishable from an empty one.

    The name says workspace, not effective: graphrag also resolves ${...}
    against the process os.environ, which this digest cannot see and the
    console does not manage. Being a sha256 of file bytes, it identifies a
    configuration without exposing any value in it (spec 7.2).
    """
```

The worker: read `{run_id}` from `job.params`; take the project lock
briefly, read both files, compute the digest, `load_config(root)`, release,
and write `started_at` + `workspace_config_revision`; `_prepare_query(project, method, config=config)`
once; loop the placeholder rows in `position` order, calling
`_execute_query` per question inside a `try/except Exception` that stores
`error` (tail-truncated, the full exception logged server-side) instead of
propagating; write each row and `jobs_repo.set_progress` between questions;
check `cancel_requested()` between questions and return
`RunResult(status="cancelled", ...)` leaving the remainder untouched; stamp
`finished_at` and return `RunResult(status="succeeded", exit_code=None, error=None, stats=None)`.

- [ ] **Step 5-7: Run, gate, commit**

```bash
cd backend && uv run pytest tests/test_test_runs.py tests/test_runner_loop.py -q
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src backend/tests/test_test_runs.py
git commit -m "$(cat <<'EOF'
feat(tests): run a question set as a cancellable background job

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 6: Run, result and rating endpoints

**Files:**
- Create: `backend/src/graphrag_ui/api/test_runs_routes.py`, `backend/src/graphrag_ui/domain/test_runs.py`
- Modify: `backend/src/graphrag_ui/api/jobs_routes.py`, `backend/src/graphrag_ui/main.py`
- Test: `backend/tests/test_test_runs_api.py` (new file)

**Interfaces:**
- Consumes: `enqueue_run` (Task 5); `TestRun`, `TestResult`, `ResultRating` (Task 1).
- Produces:
  - `domain/test_runs.py`: `RATING_SCORES = ("good", "fair", "poor")`; `RATING_ORDER: dict[str, int]`; `count_regressions(previous: Mapping[uuid, str], current: Mapping[uuid, str]) -> int` — pure, counts lineages whose newest rating is worse than the previous run's.
  - Routes: `GET /api/projects/{pid}/test-runs` (matrix source, rows keyed by lineage), `POST /api/projects/{pid}/test-runs`, `GET /api/test-runs/{rid}/results`, `PUT /api/test-results/{rid}/rating`.
  - `GET /api/projects/{pid}/jobs` gains an optional `type` query filter so the jobs page can exclude `test_run` rows **server-side**.

**Regressions are counted server-side.** The overview (slice ③) must state
the number without downloading every result; a rating distribution alone
cannot answer the question the action card asks. Putting the arithmetic in
`domain/` keeps it testable without a database.

- [ ] **Step 1: Write the failing API tests**

```python
async def test_matrix_rows_are_lineages_not_question_rows(client, two_runs_with_a_fork):
    alice, pid, lineage_id = two_runs_with_a_fork
    body = (await client.get(f"/api/projects/{pid}/test-runs", headers=alice)).json()

    assert [row["lineage_id"] for row in body["rows"]] == [lineage_id]
    cells = body["rows"][0]["cells"]
    # One row, two cells, each carrying the text ACTUALLY asked.
    assert [c["question_text"] for c in cells] == ["original", "reworded"]


async def test_rating_upsert_overwrites_and_records_the_rater(client, db_session, finished_run):
    alice, bob, pid, result_id = finished_run
    assert (await client.put(f"/api/test-results/{result_id}/rating", headers=alice,
                             json={"score": "fair", "note": ""})).status_code == 200
    r = await client.put(f"/api/test-results/{result_id}/rating", headers=bob,
                         json={"score": "good", "note": "better after reindex"})
    assert r.status_code == 200 and r.json()["score"] == "good"

    rows = (await db_session.execute(
        select(ResultRating).where(ResultRating.result_id == uuid.UUID(result_id))
    )).scalars().all()
    assert len(rows) == 1 and rows[0].note == "better after reindex"


async def test_rating_rejects_an_unknown_score(client, finished_run):
    alice, _, _, result_id = finished_run
    r = await client.put(f"/api/test-results/{result_id}/rating", headers=alice,
                         json={"score": "excellent", "note": ""})
    assert r.status_code == 422


def test_count_regressions_counts_only_downward_moves():
    prev = {"l1": "good", "l2": "fair", "l3": "poor", "l4": "good"}
    curr = {"l1": "fair", "l2": "fair", "l3": "good", "l4": "poor"}
    assert count_regressions(prev, curr) == 2  # l1 and l4


def test_count_regressions_ignores_lineages_missing_on_either_side():
    assert count_regressions({"l1": "good"}, {}) == 0
    assert count_regressions({}, {"l1": "poor"}) == 0


async def test_jobs_list_can_exclude_test_runs(client, project_with_jobs):
    alice, pid = project_with_jobs
    all_jobs = (await client.get(f"/api/projects/{pid}/jobs", headers=alice)).json()
    index_only = (await client.get(f"/api/projects/{pid}/jobs?type=index", headers=alice)).json()
    assert len(index_only) < len(all_jobs)
    assert all(j["type"] == "index" for j in index_only)


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
        method, path.format(**ids), headers=viewer,
        json=None if body is None else {k: v.format(**ids) if isinstance(v, str) else v
                                        for k, v in body.items()},
    )
    assert r.status_code == viewer_status


async def test_a_result_from_another_project_is_404_not_403(client, two_projects):
    """403 would confirm the row exists."""
    alice_a, _, result_id_b = two_projects
    r = await client.put(f"/api/test-results/{result_id_b}/rating", headers=alice_a,
                         json={"score": "good", "note": ""})
    assert r.status_code == 404
    assert r.json()["code"] != "forbidden"


async def test_reading_results_of_another_projects_run_is_404_not_403(client, two_projects):
    alice_a, run_id_b, _ = two_projects
    r = await client.get(f"/api/test-runs/{run_id_b}/results", headers=alice_a)
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_test_runs_api.py -q`
Expected: FAIL — 404 on `/test-runs`.

- [ ] **Step 3: Implement**

`domain/test_runs.py`:

```python
RATING_SCORES: tuple[str, ...] = ("good", "fair", "poor")
# Ordered best to worst; a regression is a move to a HIGHER index.
RATING_ORDER: dict[str, int] = {s: i for i, s in enumerate(RATING_SCORES)}


def count_regressions(previous: Mapping[str, str], current: Mapping[str, str]) -> int:
    """Lineages whose newest rating is worse than the previous run's.

    A lineage missing on either side counts as nothing: an unrated or
    not-yet-asked question is not a regression, and treating it as one
    would make the overview cry wolf on every new question.
    """
    return sum(
        1
        for lineage, score in current.items()
        if lineage in previous and RATING_ORDER[score] > RATING_ORDER[previous[lineage]]
    )
```

`GET /test-runs` returns `{"runs": [...], "rows": [{lineage_id, cells: [...]}]}`
where `cells` is aligned to `runs` by position, each cell carrying
`{result_id, question_text, rating, error, completed}` and `null` for a
lineage a run never asked. Default to the most recent
`MATRIX_DEFAULT_RUNS = 5` runs, overridable with `?runs=`.

`PUT /api/test-results/{rid}/rating` resolves the result's project through
`test_results.run_id → test_runs.project_id`, checks
`project:edit_content` against **that** project, and 404s on any mismatch —
never 403.

- [ ] **Step 4-6: Run, regenerate, gate, commit**

```bash
cd backend && uv run pytest tests/test_test_runs_api.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(tests): expose runs, results and human ratings

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 7: Frontend — the workbench, the matrix and ad-hoc query

**Files:**
- Create: `frontend/src/components/tests/{Workbench,RatingMatrix,AnswerView,AdhocQuery}.tsx`
- Modify: `frontend/src/components/QueryPanel.tsx` (becomes `AdhocQuery`, then the old file is deleted), `frontend/src/pages/ProjectDetail.tsx`, `frontend/src/api/types.ts`, both locales
- Test: `frontend/src/components/__tests__/QueryPanel.test.tsx` (moves to `AdhocQuery.test.tsx`, extended), `frontend/src/components/__tests__/RatingMatrix.test.tsx` (new)

**Interfaces:**
- Consumes: `GET/POST /api/projects/{pid}/test-runs`, `GET /api/projects/{pid}/question-sets`, the existing SSE route.
- Produces: `AnswerView` with props `{ answer: string; citations: Citation[]; timings: QueryTimings | null; streaming?: boolean }`; `RatingMatrix` with props `{ runs, rows, regressionsOnly, onRegressionsOnly, onCell }`.

**Two execution paths, deliberately different, one rendering.** Ad-hoc uses
the existing SSE stream (interactive, token-by-token); batch is a
`test_run` job (long, cancellable, survives navigation). They do **not**
share a search call — SSE keeps `stream_query`, the batch uses
`_execute_query` — but after Task 4 they share configuration loading, frame
loading, citation enrichment and timing assembly, which is what makes the
two renderings comparable. If the same answer rendered differently in two
places, users would reasonably suspect they had gotten different results.

**No virtualization.** Hundreds of rows × 5 columns is well within antd's
`Table`; complexity for imagined scale is complexity now for a benefit
later.

- [ ] **Step 1: Write the failing tests**

```tsx
test("matrix renders one row per lineage and one column per run", async () => {
  renderWorkbench();
  expect(await screen.findByText("Q1 保固期多長")).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader")).toHaveLength(1 + 4);  // question + 4 runs
});

test("a cell a run never asked renders as not-run, not as an empty answer", async () => {
  renderWorkbench();
  const row = (await screen.findByText("Q5 企業採購窗口")).closest("tr")!;
  expect(within(row).getAllByLabelText("未執行")).toHaveLength(2);
});

test("regressions-only uses the same definition the backend reports", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByLabelText("只看退步的"));
  expect(screen.getByText("Q3 退貨流程幾天")).toBeInTheDocument();
  expect(screen.queryByText("Q1 保固期多長")).not.toBeInTheDocument();
});

test("the launch dialog states question count and method before committing", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByRole("button", { name: "重跑整組" }));
  expect(await screen.findByText(/20 題/)).toBeInTheDocument();
  expect(screen.getByText(/local/)).toBeInTheDocument();
});

test("a job conflict names which job is running", async () => {
  renderWorkbench({ activeJob: { id: "j1", type: "index" } });
  expect(await screen.findByText(/索引作業執行中/)).toBeInTheDocument();
  // Not a dead button, and not a message implying only indexing can block.
  expect(screen.getByRole("button", { name: "重跑整組" })).toBeDisabled();
});

test("ad-hoc query still streams token by token", async () => {
  renderAdhoc();
  await userEvent.type(screen.getByPlaceholderText(/輸入問題/), "hello");
  await userEvent.click(screen.getByRole("button", { name: "執行" }));
  emitSse("chunk", "part one ");
  emitSse("chunk", "part two");
  expect(await screen.findByText("part one part two")).toBeInTheDocument();
});

test("an ad-hoc answer saves into a question set in one action", async () => {
  renderAdhoc();
  await userEvent.type(screen.getByPlaceholderText(/輸入問題/), "退貨要幾天？");
  await userEvent.click(screen.getByRole("button", { name: "執行" }));
  emitSse("chunk", "three working days");
  emitSse("done", TIMINGS);

  await userEvent.click(await screen.findByRole("button", { name: "存成題目" }));
  await userEvent.click(await screen.findByRole("option", { name: "客服常問 20 題" }));
  await userEvent.click(screen.getByRole("button", { name: "確定" }));

  expect(postedTo).toBe("/api/projects/p1/question-sets/s1/questions");
  expect(postedBody).toEqual({ text: "退貨要幾天？" });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- RatingMatrix AdhocQuery`
Expected: FAIL — the components do not exist.

- [ ] **Step 3: Implement**

`AdhocQuery` is today's `QueryPanel` with its rendering lifted into
`AnswerView`; keep the `EventSource` lifecycle exactly as it is (close on
unmount, no auto-reconnect — a reconnect would replay the query and
double-charge the rate limit). `Workbench` owns the set picker, the launch
dialog, the run list and the matrix, and switches between matrix and ad-hoc
mode. `ProjectDetail` swaps its `query` tab for the workbench (the routed
sidebar arrives in slice ③).

- [ ] **Step 4: Both locales, then the frontend gate**

Run: `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(tests): add the retrieval test workbench and rating matrix

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 8: Frontend — cell drawer, keyboard rating, run diff

**Files:**
- Create: `frontend/src/components/tests/{ResultDrawer,RunDiff}.tsx`, `frontend/src/components/tests/sentenceDiff.ts`
- Modify: `frontend/src/components/tests/RatingMatrix.tsx`, both locales
- Test: `frontend/src/components/__tests__/sentenceDiff.test.ts` (new), `RatingMatrix.test.tsx` (extend)

**Interfaces:**
- Consumes: `GET /api/test-runs/{rid}/results`, `PUT /api/test-results/{rid}/rating`.
- Produces: `sentenceDiff(a: string, b: string) -> { text: string; side: "both" | "left" | "right" }[]`; `ResultDrawer` with props `{ resultId: string | null; onClose: () => void; onRated: () => void }`.

**Rating must be fast.** With the drawer open, `1`/`2`/`3` rate and advance.
Rating 20 answers at three mouse clicks each is how a feature goes unused;
this is the least visible decision in the slice and the one that determines
whether it gets used.

**Diff granularity is sentences, not characters.** GraphRAG answers are
prose; character diffs bury the real change in noise. Sentence splitting and
comparison are a pure frontend function with unit tests.

- [ ] **Step 1: Write the failing tests**

```ts
test("splits on sentence boundaries, not on characters", () => {
  const out = sentenceDiff("A one. B two. C three.", "A one. B changed. C three.");
  expect(out.filter((s) => s.side === "both").map((s) => s.text.trim()))
    .toEqual(["A one.", "C three."]);
  expect(out.filter((s) => s.side === "left").map((s) => s.text.trim())).toEqual(["B two."]);
  expect(out.filter((s) => s.side === "right").map((s) => s.text.trim())).toEqual(["B changed."]);
});

test("identical answers produce no differing segments", () => {
  expect(sentenceDiff("Same. Text.", "Same. Text.").every((s) => s.side === "both")).toBe(true);
});

test("an empty side yields every sentence on the other", () => {
  expect(sentenceDiff("", "Only right.").map((s) => s.side)).toEqual(["right"]);
});

test("CJK full stops are sentence boundaries too", () => {
  const out = sentenceDiff("第一句。第二句。", "第一句。改過了。");
  expect(out.filter((s) => s.side === "both").map((s) => s.text)).toEqual(["第一句。"]);
});
```

```tsx
test("keys 1/2/3 rate the open result and advance to the next", async () => {
  renderMatrixWithDrawer();
  await userEvent.keyboard("1");
  expect(rated).toEqual([{ resultId: "r1", score: "good" }]);
  expect(await screen.findByText("Q2 如何申請發票")).toBeInTheDocument();
});

test("editing a question that has runs warns before forking", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByRole("button", { name: "編輯題目" }));
  expect(await screen.findByText(/會建立新版本，過去的執行仍保留原本的題目文字/))
    .toBeInTheDocument();
});

test("two selected cells open the side-by-side diff", async () => {
  renderMatrix();
  await userEvent.click(await screen.findByLabelText("Q3 × #12"));
  await userEvent.click(screen.getByLabelText("Q3 × #15"));
  expect(await screen.findByRole("heading", { name: /並排比較/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- sentenceDiff RatingMatrix`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`sentenceDiff` splits on `/(?<=[.!?。！？])\s*/`, keeps the delimiter, and
runs a standard LCS over the sentence arrays. Pure, no DOM, no React.

`ResultDrawer` shows the question text **as asked**, the answer, the
citations, the rating control and the note. `keydown` on `1`/`2`/`3` fires
the rating mutation and advances; the handler is bound to the drawer, not
`document`, so typing in the note field never rates.

- [ ] **Step 4: Both locales; frontend gate**

Run: `cd frontend && npm test && npm run lint && npx tsc -b --noEmit && npm run build`

- [ ] **Step 5: Documentation and release notes**

- `README.md`: the retrieval-testing loop; mirror into `docs/zh-TW/README.md`
  in the same commit.
- Release notes: test runs and index jobs are **mutually exclusive per
  project**, including when the global `MAX_CONCURRENT_JOBS` budget is free
  — and say so in both directions, because the jobs page shows the same
  conflict from the other side. Note that the batch runs inside the API
  process, as interactive queries already do: many sequential queries, not a
  new class of load, but sustained, with `MAX_CONCURRENT_JOBS` as the
  throttle.
- No new environment variables.

- [ ] **Step 6: Full gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
cd ../frontend && npm test && npm run lint && npx tsc -b --noEmit && npm run build
git add frontend/src README.md docs/zh-TW
git commit -m "$(cat <<'EOF'
feat(tests): add the result drawer, keyboard rating and run diff

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

## Slice exit criteria

Stopping here leaves a coherent product: a question set can be re-run in one
action against the current index as a cancellable background job, answers
are human-rated, and the matrix shows quality trend per question across runs
without historical rows changing meaning when the set is edited. Slice ③
(`docs/superpowers/plans/2026-09-06-kb-slice3-wiring.md`) adds the routed
sidebar, the health overview and its action card, and closes the
citation-to-passage loop that makes a bad answer traceable to the document
at fault.
