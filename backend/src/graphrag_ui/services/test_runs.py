"""Test runs (spec 5.3/7.2/7.3): enqueue and the read models. The worker
that executes a run lives in services/test_run_worker.py, ratings in
services/ratings.py.

The manifest is materialized at enqueue, not at execution: between POST
/test-runs and the worker claiming the job the set can be edited, so the
placeholder test_results rows ARE the manifest — ordered, immutable, and
already the thing the worker fills in. jobs.params carries only {run_id}.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.models import (
    Job,
    Project,
    Question,
    ResultRating,
    TestResult,
    TestRun,
    User,
)
from graphrag_ui.domain.jobs import CLI_JOB_TYPES
from graphrag_ui.services.audit import audit
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.jobs import JobConflictError
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.questions import live_questions

logger = logging.getLogger(__name__)


class EmptyQuestionSetError(CodedServiceError, RuntimeError):
    """The set has no live questions — a run of nothing is a caller error."""

    code = "question_set_empty"


async def _commit_manifest(session: AsyncSession) -> None:
    # Module-level seam: the lock-barrier tests park between the manifest
    # flush and the commit by patching this one function.
    await session.commit()


async def _last_successful_index_job(
    session: AsyncSession, project_id: uuid.UUID
) -> uuid.UUID | None:
    # The last successful index/update at enqueue: it labels a matrix column
    # and is what makes runs comparable. Nullable — a project queried before
    # any index job exists has none.
    return (
        await session.execute(
            select(Job.id)
            .where(
                Job.project_id == project_id,
                Job.type.in_(CLI_JOB_TYPES),
                Job.status == "succeeded",
            )
            .order_by(Job.finished_at.desc().nulls_last(), Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def enqueue_run(
    session: AsyncSession, project: Project, set_id: uuid.UUID, method: str, actor: User
) -> TestRun:
    """Run row + job row + one placeholder result per question, ONE
    transaction under the project lock (spec 5.3).

    The placeholder rows ARE the manifest. jobs.params carries only
    {run_id}: a set id would let the worker read a set that has changed
    since enqueue.
    """
    await lock_project(session, project.id)
    questions = await live_questions(session, project.id, set_id)
    if not questions:
        raise EmptyQuestionSetError(str(set_id))
    index_job_id = await _last_successful_index_job(session, project.id)
    project_id = str(project.id)  # snapshot: rollback() expires instances
    try:
        job = await jobs_repo.insert_job(
            session,
            project_id=project.id,
            type="test_run",
            method=method,
            argv=[],
            queued_by=actor.id,
        )
        run = TestRun(
            project_id=project.id,
            set_id=set_id,
            job_id=job.id,
            index_job_id=index_job_id,
            method=method,
        )
        session.add(run)
        await session.flush()
        session.add_all(
            TestResult(run_id=run.id, question_id=q.id, position=i, question_text=q.text)
            for i, q in enumerate(questions)
        )
        # Plain JSONB columns without change tracking: assign, never mutate.
        job.params = {"run_id": str(run.id)}
        job.progress = {"done": 0, "total": len(questions)}
        await audit(
            session,
            actor.id,
            "test_run.enqueued",
            "project",
            str(project.id),
            {"run_id": str(run.id), "set_id": str(set_id), "questions": len(questions)},
        )
        await _commit_manifest(session)
    except IntegrityError:
        # jobs_one_active_per_project fired: an index, an update, or another
        # test run holds the project. Never check-then-insert.
        await session.rollback()
        raise JobConflictError(project_id) from None
    return run


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> TestRun | None:
    return await session.get(TestRun, run_id)


async def list_results(
    session: AsyncSession, run_id: uuid.UUID
) -> list[tuple[TestResult, ResultRating | None]]:
    """A run's results in ask order, each with its current rating (spec 5.3)."""
    rows = (
        await session.execute(
            select(TestResult, ResultRating)
            .outerjoin(ResultRating, ResultRating.result_id == TestResult.id)
            .where(TestResult.run_id == run_id)
            .order_by(TestResult.position)
        )
    ).all()
    return [(result, rating) for result, rating in rows]


async def run_matrix(
    session: AsyncSession, project_id: uuid.UUID, limit: int
) -> tuple[list[TestRun], list[tuple[uuid.UUID, list[tuple[TestResult, str | None] | None]]]]:
    """The matrix window: the `limit` most recent runs OLDEST-first, plus
    per-lineage cell lists aligned to them by position (spec 5.3).

    Rows are lineages, not question rows: an edit forks a new question on
    the same lineage, which keeps one row while each cell carries the text
    actually asked. A lineage a run never asked is None at that position.
    Rows keep first-introduction order: the earliest (window run, position)
    pair a lineage appeared at, so the matrix reads top-to-bottom as the set
    grew.
    """
    newest_first = list(
        (
            await session.execute(
                select(TestRun)
                .join(Job, Job.id == TestRun.job_id)
                .where(TestRun.project_id == project_id)
                .order_by(Job.queued_at.desc(), TestRun.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    runs = list(reversed(newest_first))
    index = {run.id: i for i, run in enumerate(runs)}
    fetched = (
        await session.execute(
            select(TestResult, Question.lineage_id, ResultRating.score)
            .join(Question, Question.id == TestResult.question_id)
            .outerjoin(ResultRating, ResultRating.result_id == TestResult.id)
            .where(TestResult.run_id.in_(index))
            .order_by(TestResult.position)
        )
    ).all()
    by_lineage: dict[uuid.UUID, list[tuple[TestResult, str | None] | None]] = {}
    first_seen: dict[uuid.UUID, tuple[int, int]] = {}
    for result, lineage_id, score in fetched:
        i = index[result.run_id]
        cells = by_lineage.setdefault(lineage_id, [None] * len(runs))
        # One live question per lineage per set manifest, so a lineage
        # appears at most once per run — never overwritten.
        cells[i] = (result, score)
        first_seen[lineage_id] = min(
            first_seen.get(lineage_id, (i, result.position)), (i, result.position)
        )
    ordered = sorted(by_lineage.items(), key=lambda item: first_seen[item[0]])
    return runs, [(lineage_id, cells) for lineage_id, cells in ordered]
