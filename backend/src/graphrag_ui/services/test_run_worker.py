"""Execution of test_run jobs (spec 5.3/7.2/7.3) — the runner_loop side
of a test run; services/test_runs.py enqueues it and reads it back.

Recording the configuration honestly takes more than a settings hash: the
effective configuration also depends on .env, and the digest and the load
are two independent reads — the worker therefore takes the project lock
briefly at start, computes the digest and loads the config inside it, then
releases. The lock is never held for the run.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import RunResult
from graphrag_ui.adapters.models import Project, TestResult, TestRun
from graphrag_ui.services import query as query_service
from graphrag_ui.services.citations import CitationMemo, read_generation
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.query import execute_query, prepare_query

logger = logging.getLogger(__name__)

# What a failed question's row says. Fixed, like the interactive query
# path's messages (spec A7): the exception carries provider error bodies,
# URLs and workspace paths, and every project viewer reads this row — the
# cause stays in the server log with the run id and position (R2-07).
QUESTION_FAILED_ERROR = "query failed"


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

    The name says workspace, not effective: settings.yaml and .env are the
    whole placeholder input (adapters/workspace_env.py), but the graphrag
    version and the operator's subprocess passthrough (proxy, CA bundle)
    are outside the digest. Being a sha256 of file bytes, it identifies a
    configuration without exposing any value in it (spec 7.2).
    """
    digest = hashlib.sha256()
    for name in ("settings.yaml", ".env"):
        path = root / name
        data = path.read_bytes() if path.is_file() else None
        digest.update(_framed(name, data))
    return digest.hexdigest()


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
        config = await asyncio.to_thread(_load_run_config, root)
        run.started_at = datetime.now(UTC)
        run.workspace_config_revision = revision
        await s.commit()

    # G0 ONCE for the whole run, read before the preamble frame load
    # (spec 7.4): every question's G1 is evaluated against this one, so a
    # rebuild landing anywhere inside the run withholds every later link.
    # The memo is run-scoped: at most one resolver call per question, only
    # for ids no earlier question already resolved, and one baseline read.
    g0 = await read_generation(project.id)
    memo = CitationMemo()

    # One preamble for the whole run: Prepared.config is reused for every
    # question, so an edit to settings.yaml or .env mid-batch cannot change
    # what the later questions execute against.
    prepared = await prepare_query(project, run.method, config=config)

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
            body = await execute_query(
                prepared, run.method, row.question_text, None, g0=g0, memo=memo
            )
            answer, citations, timings, error = (
                body["answer"],
                body["citations"],
                body["timings"],
                None,
            )
        except Exception:  # one bad question must not fail the run
            logger.exception("test_run question failed (run %s, position %s)", run.id, row.position)
            answer, citations, timings = None, None, None
            error = QUESTION_FAILED_ERROR
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
