"""Background job execution loop (spec §6.3): claim from the PG queue under
the global concurrency cap, heartbeat while the subprocess runs, execute it
via adapters.IndexRunner, and write terminal state. Stale running jobs (pod
restarts) are reconciled at startup and periodically. Runs inside the API
process — identical behavior in compose and K8s (spec §3)."""

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import IndexRunner, RunResult, log_path_for
from graphrag_ui.adapters.models import Job
from graphrag_ui.config import get_settings
from graphrag_ui.services.projects import _ws_path

_HEARTBEAT_S = 10.0
_STALE_AFTER_S = 60.0
_CLAIM_POLL_S = 1.0
_RECONCILE_EVERY_S = 60.0
_CANCEL_POLL_S = 1.0

logger = logging.getLogger(__name__)

# Tasks spawned by run_loop; the done-callback discards so nothing leaks
# across loop restarts. Unfinished subprocesses are NOT awaited on shutdown —
# next boot's reconcile marks them failed(interrupted) (spec §10).
_executing: set[asyncio.Task] = set()


def worker_id() -> str:
    return f"{socket.gethostname()}#{os.getpid()}"


async def reconcile_stale() -> int:
    """Running jobs whose heartbeat stopped ~1 minute ago are dead workers
    (pod restart); finish them as failed(interrupted). Returns the count."""
    cutoff = datetime.now(UTC) - timedelta(seconds=_STALE_AFTER_S)
    async with get_session_factory()() as s:
        stale = await jobs_repo.find_stale_running(s, cutoff)
        for job in stale:
            await jobs_repo.finish(
                s, job.id, "failed(interrupted)",
                error="worker heartbeat timeout; job interrupted")
    return len(stale)


async def _cancel_requested_in_db(job_id: uuid.UUID) -> bool:
    """Fresh-session read of cancel_requested_at — no identity-map staleness."""
    async with get_session_factory()() as s:
        row = (await s.execute(
            select(Job.cancel_requested_at).where(Job.id == job_id))
        ).scalar_one_or_none()
    return row is not None


async def _execute(job_id: uuid.UUID) -> None:
    # IndexRunner resolves from this module's globals: tests monkeypatch
    # runner_loop.IndexRunner (dry_run precedent).
    wid = worker_id()
    async with get_session_factory()() as s:
        job = await jobs_repo.get_job(s, job_id)
        if job is None or job.status != "running":
            return  # already finished or reconciled elsewhere
        argv, job_type, project_id = job.argv, job.type, job.project_id
    root = _ws_path(project_id)
    hb_stop = asyncio.Event()
    state: dict[str, bool] = {"cancelled": False}

    async def watch() -> None:
        """Owns the DB-side cadence: beat every _HEARTBEAT_S, poll
        cancel_requested_at every _CANCEL_POLL_S. IndexRunner's heartbeat
        parameter is never invoked by run() — the lambda passed below is a
        placeholder its signature requires; all cadence lives here."""
        last_beat = float("-inf")  # force a beat on the first iteration
        loop = asyncio.get_running_loop()
        while not hb_stop.is_set():
            try:
                if loop.time() - last_beat >= _HEARTBEAT_S:
                    async with get_session_factory()() as s:
                        await jobs_repo.heartbeat(s, job_id, wid)
                    last_beat = loop.time()
                if await _cancel_requested_in_db(job_id):
                    state["cancelled"] = True
            except Exception:  # one failed poll must not kill the watcher
                logger.warning("watch poll failed for job %s", job_id,
                               exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(hb_stop.wait(), timeout=_CANCEL_POLL_S)

    hb_task = asyncio.create_task(watch())
    try:
        res = await IndexRunner().run(
            argv=argv, root=root, log_path=log_path_for(root, job_id),
            job_type=job_type,
            heartbeat=lambda: asyncio.sleep(0),  # placeholder: run() never awaits it
            cancel_requested=lambda: state["cancelled"])
    except Exception as exc:  # the job must reach a terminal state regardless
        logger.exception("job execution crashed: %s", job_id)
        res = RunResult(status="failed", exit_code=None,
                        error=f"internal error: {exc!r}", stats=None)
    finally:
        hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        except Exception:  # a dead watcher must not block finish()
            logger.warning("watch task ended with an error", exc_info=True)
    async with get_session_factory()() as s:
        await jobs_repo.finish(s, job_id, res.status,
                               exit_code=res.exit_code, error=res.error,
                               stats=res.stats)


async def run_loop(stop: asyncio.Event) -> None:
    cap = get_settings().max_concurrent_jobs
    if cap <= 0:
        return  # disabled (tests / dedicated API-only replica)
    loop = asyncio.get_running_loop()
    last_reconcile: float | None = None  # reconcile on the first iteration
    while not stop.is_set():
        try:
            now = loop.time()
            if last_reconcile is None or now - last_reconcile >= _RECONCILE_EVERY_S:
                await reconcile_stale()
                last_reconcile = loop.time()
            # count-then-claim is not atomic across pods: during a rolling
            # update the global cap can be exceeded transiently by up to
            # pods-1 jobs. Per pod it is exact, and the per-project mutex is
            # DB-enforced (partial unique index), so transient over-cap is a
            # resource blip, never a correctness issue.
            async with get_session_factory()() as s:
                running = await jobs_repo.count_running(s)
                job = (await jobs_repo.claim_next(s, worker_id())
                       if running < cap else None)
            if job is not None:
                t = asyncio.create_task(_execute(job.id))
                _executing.add(t)
                t.add_done_callback(_executing.discard)
        except Exception:  # the loop must survive any single-iteration failure
            logger.exception("runner loop iteration failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=_CLAIM_POLL_S)
