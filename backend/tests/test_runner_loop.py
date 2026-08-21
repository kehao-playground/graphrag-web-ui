import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import RunResult
from graphrag_ui.adapters.models import Job, Project, User
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from graphrag_ui.services import runner_loop


class FakeRunner:
    """Records kwargs; simulates a subprocess result without forking."""

    def __init__(self, result: RunResult | None = None,
                 exc: Exception | None = None, sleep_s: float = 0.05):
        self.result = result
        self.exc = exc
        self.sleep_s = sleep_s
        self.calls: list[dict] = []

    async def run(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(self.sleep_s)
        if self.exc is not None:
            raise self.exc
        return self.result


def _ok() -> RunResult:
    return RunResult(status="succeeded", exit_code=0, error=None, stats=None)


async def _seed_job() -> uuid.UUID:
    """One user + project + queued job; returns the job id."""
    async with get_session_factory()() as s:
        u = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x",
                 display_name="u")
        s.add(u)
        await s.flush()
        p = Project(name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id,
                    input_file_type="text")
        s.add(p)
        await s.flush()
        job = await jobs_repo.insert_job(
            s, project_id=p.id, type="index", method="fast",
            argv=["index", "--root", "/ws", "--method", "fast"], queued_by=u.id)
        await s.commit()
        return job.id


async def _claim(job_id: uuid.UUID, wid: str = "w-seed") -> None:
    async with get_session_factory()() as s:
        await jobs_repo.claim_next(s, wid)


async def _get(job_id: uuid.UUID) -> Job:
    async with get_session_factory()() as s:
        return await jobs_repo.get_job(s, job_id)


async def test_execute_runs_and_finishes(app, monkeypatch):
    fake = FakeRunner(RunResult(status="succeeded", exit_code=0, error=None,
                                stats={"num_documents": 1}))
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    job_id = await _seed_job()
    await _claim(job_id)
    await runner_loop._execute(job_id)
    job = await _get(job_id)
    assert job.status == "succeeded"
    assert job.exit_code == 0
    assert job.stats == {"num_documents": 1}
    assert fake.calls[0]["argv"] == ["index", "--root", "/ws", "--method", "fast"]
    assert fake.calls[0]["job_type"] == "index"
    # the heartbeat task beat at least once before finishing
    assert job.worker_id == runner_loop.worker_id()


async def test_execute_crash_marks_failed(app, monkeypatch):
    fake = FakeRunner(exc=RuntimeError("boom"))
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    job_id = await _seed_job()
    await _claim(job_id)
    await runner_loop._execute(job_id)
    job = await _get(job_id)
    assert job.status == "failed"
    assert "boom" in (job.error or "")


async def test_execute_ignores_non_running(app, monkeypatch):
    fake = FakeRunner(_ok())
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    job_id = await _seed_job()  # stays queued — never claimed
    await runner_loop._execute(job_id)
    assert fake.calls == []
    assert (await _get(job_id)).status == "queued"


async def test_loop_disabled_returns_immediately(app, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    get_settings.cache_clear()
    stop = asyncio.Event()
    started = time.monotonic()
    await asyncio.wait_for(runner_loop.run_loop(stop), timeout=2)
    assert time.monotonic() - started < 1


async def test_loop_claims_and_executes(app, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    get_settings.cache_clear()
    fake = FakeRunner(_ok())
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    job_id = await _seed_job()
    stop = asyncio.Event()
    loop_task = asyncio.create_task(runner_loop.run_loop(stop))

    async def _wait_terminal() -> str:
        status = (await _get(job_id)).status
        while status not in TERMINAL_STATUSES:
            await asyncio.sleep(0.2)
            status = (await _get(job_id)).status
        return status

    assert await asyncio.wait_for(_wait_terminal(), timeout=10) == "succeeded"
    assert fake.calls, "loop must execute the job through IndexRunner"
    stop.set()
    await asyncio.wait_for(loop_task, timeout=5)
    assert not runner_loop._executing  # done-callback drained the tracking set


async def test_loop_respects_cap(app, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    get_settings.cache_clear()
    fake = FakeRunner(_ok())
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    busy_id = await _seed_job()
    await _claim(busy_id, "w-busy")  # occupies the single concurrency slot
    other_id = await _seed_job()  # queued behind the cap
    stop = asyncio.Event()
    loop_task = asyncio.create_task(runner_loop.run_loop(stop))
    await asyncio.sleep(2.5)  # ≥2 claim polls; the slot stays occupied
    assert (await _get(other_id)).status == "queued"
    assert fake.calls == []
    stop.set()
    await asyncio.wait_for(loop_task, timeout=5)


async def test_reconcile_marks_stale(app):
    job_id = await _seed_job()
    await _claim(job_id, "w-dead")
    # heartbeat is fresh — force-stale by backdating it
    async with get_session_factory()() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(
            heartbeat_at=datetime.now(UTC) - timedelta(seconds=120)))
        await s.commit()
    assert await runner_loop.reconcile_stale() == 1
    job = await _get(job_id)
    assert job.status == "failed(interrupted)"
    assert job.exit_code is None
    assert "heartbeat" in (job.error or "")


async def test_reconcile_leaves_fresh_running(app):
    job_id = await _seed_job()
    await _claim(job_id, "w-alive")
    assert await runner_loop.reconcile_stale() == 0
    assert (await _get(job_id)).status == "running"


async def test_execute_survives_watch_poll_failure(app, monkeypatch):
    fake = FakeRunner(_ok(), sleep_s=1.5)  # span ≥2 watch iterations
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    real_heartbeat = jobs_repo.heartbeat
    flaky_calls = {"n": 0}

    async def flaky_heartbeat(session, job_id, worker_id, pid=None):
        flaky_calls["n"] += 1
        if flaky_calls["n"] == 1:
            raise RuntimeError("db blip")
        return await real_heartbeat(session, job_id, worker_id, pid)

    monkeypatch.setattr(jobs_repo, "heartbeat", flaky_heartbeat)
    job_id = await _seed_job()
    await _claim(job_id)
    await runner_loop._execute(job_id)
    job = await _get(job_id)
    assert flaky_calls["n"] >= 2  # first beat raised, watcher retried and recovered
    assert job.status == "succeeded"
    assert job.worker_id == runner_loop.worker_id()  # recovered beat landed
