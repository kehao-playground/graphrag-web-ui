"""Batch execution of test_run jobs (spec 5.3/7.2/7.3).

The manifest is materialized at enqueue, not at execution: between POST
/test-runs and the worker claiming the job the set can be edited, so the
placeholder test_results rows ARE the manifest — ordered, immutable, and
already the thing the worker fills in. jobs.params carries only {run_id}.

Recording the configuration honestly takes more than a settings hash: the
effective configuration also depends on .env, and the digest and the load
are two independent reads — the worker therefore takes the project lock
briefly at start, computes the digest and loads the config inside it, then
releases. The lock is never held for the run.
"""

import hashlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import RunResult
from graphrag_ui.adapters.models import Job, Project, TestResult, TestRun, User
from graphrag_ui.services import query as query_service
from graphrag_ui.services.audit import audit
from graphrag_ui.services.jobs import JobConflictError
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.query import _execute_query, _prepare_query
from graphrag_ui.services.questions import live_questions

logger = logging.getLogger(__name__)

# Per-question error tails: the full exception stays in the server log; the
# row keeps the discriminating end of the message (QueryError detail is
# already a tail — this bounds any exception type).
_ERROR_TAIL_CHARS = 500

_INDEX_JOB_TYPES = ("index", "update")


class EmptyQuestionSetError(RuntimeError):
    """The set has no live questions — a run of nothing is a caller error."""


async def _commit_manifest(session: AsyncSession) -> None:
    # Module-level seam: the lock-barrier tests park between the manifest
    # flush and the commit by patching this one function.
    await session.commit()


def _load_run_config(root: Path) -> Any:
    # Module-level seam for the digest-vs-load interleaving test. Resolves
    # query.load_config at call time so its tests can count loads.
    return query_service.load_config(root)


def _framed(name: str, data: bytes | None) -> bytes:
    length = -1 if data is None else len(data)
    return name.encode() + b"\n" + str(length).encode() + b"\n" + (data or b"")


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
    digest = hashlib.sha256()
    for name in ("settings.yaml", ".env"):
        path = root / name
        data = path.read_bytes() if path.is_file() else None
        digest.update(_framed(name, data))
    return digest.hexdigest()


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
                Job.type.in_(_INDEX_JOB_TYPES),
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


async def execute_test_run(
    job_id: uuid.UUID, root: Path, *, cancel_requested: Callable[[], bool]
) -> RunResult:
    """Execute one test_run job to a terminal RunResult (runner_loop contract).

    Short-lived sessions throughout, mirroring runner_loop. The project lock
    is held only for the config capture at start; each question's result row
    and its progress tick commit together between questions, so a crash
    leaves at most the current question unanswered.
    """
    factory = get_session_factory()
    async with factory() as s:
        job = await jobs_repo.get_job(s, job_id)
        run_id = (job.params or {}).get("run_id") if job is not None else None
        run = await s.get(TestRun, uuid.UUID(run_id)) if run_id else None
        if job is None or run is None:
            return RunResult(
                status="failed",
                exit_code=None,
                error="test_run job does not reference a known run",
                stats=None,
            )
        project = await s.get(Project, run.project_id)
        if project is None:
            return RunResult(
                status="failed", exit_code=None, error="run references no project", stats=None
            )
        # Brief lock: digest and load land in the same critical section, so
        # the recorded revision cannot describe a configuration the run did
        # not load (spec 7.2). Released at this transaction's commit.
        await lock_project(s, project.id)
        revision = workspace_config_revision(root)
        config = _load_run_config(root)
        run.started_at = datetime.now(UTC)
        run.workspace_config_revision = revision
        await s.commit()

    # One preamble for the whole run: Prepared.config is reused for every
    # question, so an edit to settings.yaml or .env mid-batch cannot change
    # what the later questions execute against.
    prepared = await _prepare_query(project, run.method, config=config)

    async with factory() as s:
        placeholders = list(
            (
                await s.execute(
                    select(TestResult)
                    .where(TestResult.run_id == run.id)
                    .order_by(TestResult.position)
                )
            )
            .scalars()
            .all()
        )
    total = len(placeholders)
    done = 0
    cancelled = False
    for row in placeholders:
        if cancel_requested():
            # Leave the remainder honestly NULL — the matrix renders it as
            # not run, not as an empty answer (spec 5.3).
            cancelled = True
            break
        try:
            body = await _execute_query(prepared, run.method, row.question_text, None)
            answer, citations, timings, error = (
                body["answer"],
                body["citations"],
                body["timings"],
                None,
            )
        except Exception as exc:  # one bad question must not fail the run
            logger.exception("test_run question failed (run %s, position %s)", run.id, row.position)
            answer, citations, timings = None, None, None
            error = (str(exc) or repr(exc))[-_ERROR_TAIL_CHARS:]
        # Row and progress tick in ONE commit: done can never advertise a
        # question whose answer is not yet durable.
        async with factory() as s:
            done += 1
            await s.execute(
                update(TestResult)
                .where(TestResult.id == row.id)
                .values(
                    answer=answer,
                    citations=citations,
                    timings=timings,
                    error=error,
                    completed_at=datetime.now(UTC),
                )
            )
            await jobs_repo.set_progress(s, job_id, done, total)

    async with factory() as s:
        await s.execute(
            update(TestRun).where(TestRun.id == run.id).values(finished_at=datetime.now(UTC))
        )
        await s.commit()

    return RunResult(
        status="cancelled" if cancelled else "succeeded", exit_code=None, error=None, stats=None
    )
