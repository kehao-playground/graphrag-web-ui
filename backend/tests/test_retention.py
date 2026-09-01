"""Retention policies (spec §6.3): job log sweeps, update_output pruning,
runner prune-after-update, and /api/ready disk fields."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from graphrag_ui.adapters import jobs_repo
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import RunResult
from graphrag_ui.adapters.models import Job, Project, User
from graphrag_ui.config import get_settings
from graphrag_ui.services import runner_loop
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.retention import prune_update_output, sweep_all, sweep_job_logs

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


class FakeRunner:
    """Records kwargs; simulates a subprocess result without forking."""

    def __init__(self, result: RunResult):
        self.result = result
        self.calls: list[dict] = []

    async def run(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(0.01)
        return self.result


async def _seed_finished_job(
    status: str, finished_at: datetime, *, type: str = "index", with_log: bool = True
) -> tuple[uuid.UUID, Path]:
    """User + project + terminal job with an explicit finished_at; returns
    (job_id, log_path) — the log file is created unless with_log=False."""
    async with get_session_factory()() as s:
        u = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
        s.add(u)
        await s.flush()
        p = Project(
            name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id, input_file_type="text"
        )
        s.add(p)
        await s.flush()
        job = await jobs_repo.insert_job(
            s,
            project_id=p.id,
            type=type,
            method="fast",
            argv=["index", "--root", "/ws", "--method", "fast"],
            queued_by=u.id,
        )
        await s.commit()
        await jobs_repo.claim_next(s, "w-seed")
        await jobs_repo.finish(s, job.id, status)
        # finish() stamps func.now(); tests need a controlled clock.
        await s.execute(update(Job).where(Job.id == job.id).values(finished_at=finished_at))
        await s.commit()
        root = ws_path(p.id)
        log = root / "logs" / "jobs" / f"{job.id}.log"
        if with_log:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("log tail\n")
        return job.id, log


async def _sweep(now: datetime) -> dict:
    async with get_session_factory()() as s:
        return await sweep_job_logs(s, now)


async def test_sweep_deletes_expired_succeeded_log(app):
    job_id, log = await _seed_finished_job("succeeded", NOW - timedelta(days=31))
    assert log.exists()
    res = await _sweep(NOW)
    assert res == {"deleted_logs": 1}
    assert not log.exists()
    async with get_session_factory()() as s:  # DB row must persist
        assert (await jobs_repo.get_job(s, job_id)).status == "succeeded"


async def test_sweep_keeps_failed_log_under_90d_policy(app):
    _, log = await _seed_finished_job("failed", NOW - timedelta(days=31))
    res = await _sweep(NOW)
    assert res == {"deleted_logs": 0}
    assert log.exists()


async def test_sweep_deletes_failed_log_after_90d(app):
    _, log = await _seed_finished_job("failed", NOW - timedelta(days=91))
    res = await _sweep(NOW)
    assert res == {"deleted_logs": 1}
    assert not log.exists()


async def test_sweep_keeps_fresh_log(app):
    _, log = await _seed_finished_job("succeeded", NOW - timedelta(days=29))
    res = await _sweep(NOW)
    assert res == {"deleted_logs": 0}
    assert log.exists()


async def test_sweep_missing_log_file_is_noop(app):
    await _seed_finished_job("succeeded", NOW - timedelta(days=31), with_log=False)
    res = await _sweep(NOW)
    assert res == {"deleted_logs": 0}


def _make_update_output(root: Path, *names: str) -> None:
    base = root / "update_output"
    for n in names:
        (base / n).mkdir(parents=True, exist_ok=True)


def test_prune_keeps_newest_two_of_four(tmp_path):
    _make_update_output(
        tmp_path, "20260801-120000", "20260802-120000", "20260803-120000", "20260804-120000"
    )
    deleted = prune_update_output(tmp_path, 2)
    assert deleted == 2
    base = tmp_path / "update_output"
    assert sorted(d.name for d in base.iterdir()) == ["20260803-120000", "20260804-120000"]


def test_prune_missing_dir_is_noop(tmp_path):
    assert prune_update_output(tmp_path, 2) == 0


async def test_sweep_all_prunes_and_sweeps(app, tmp_path):
    _, log = await _seed_finished_job("succeeded", NOW - timedelta(days=31))
    root = log.parents[2]  # <workspaces>/<project_id>
    _make_update_output(root, "20260801-120000", "20260802-120000", "20260803-120000")
    res = await sweep_all()
    assert res["deleted_logs"] == 1
    assert res["pruned_dirs"] == 1
    assert not log.exists()
    assert len(list((root / "update_output").iterdir())) == 2


async def test_sweep_all_safe_on_empty_workspaces(app):
    res = await sweep_all()
    assert res == {"deleted_logs": 0, "pruned_dirs": 0}


async def _seed_running_job(type: str) -> tuple[uuid.UUID, Path]:
    async with get_session_factory()() as s:
        u = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
        s.add(u)
        await s.flush()
        p = Project(
            name="p", slug=f"s-{uuid.uuid4().hex[:8]}", owner_id=u.id, input_file_type="text"
        )
        s.add(p)
        await s.flush()
        job = await jobs_repo.insert_job(
            s,
            project_id=p.id,
            type=type,
            method="fast",
            argv=["update", "--root", "/ws", "--method", "fast"],
            queued_by=u.id,
        )
        await s.commit()
        await jobs_repo.claim_next(s, "w-seed")
        return job.id, ws_path(p.id)


async def test_execute_prunes_after_successful_update(app, monkeypatch):
    job_id, root = await _seed_running_job("update")
    _make_update_output(root, "20260801-120000", "20260802-120000", "20260803-120000")
    monkeypatch.setattr(
        runner_loop,
        "IndexRunner",
        lambda: FakeRunner(RunResult(status="succeeded", exit_code=0, error=None, stats=None)),
    )
    await runner_loop._execute(job_id)
    async with get_session_factory()() as s:
        assert (await jobs_repo.get_job(s, job_id)).status == "succeeded"
    # keep_latest=2 (default settings): 3 dirs → 1 pruned
    assert len(list((root / "update_output").iterdir())) == 2


async def test_execute_no_prune_for_index_job(app, monkeypatch):
    job_id, root = await _seed_running_job("index")
    _make_update_output(root, "20260801-120000", "20260802-120000", "20260803-120000")
    monkeypatch.setattr(
        runner_loop,
        "IndexRunner",
        lambda: FakeRunner(RunResult(status="succeeded", exit_code=0, error=None, stats=None)),
    )
    await runner_loop._execute(job_id)
    async with get_session_factory()() as s:
        assert (await jobs_repo.get_job(s, job_id)).status == "succeeded"
    assert len(list((root / "update_output").iterdir())) == 3


async def test_ready_reports_disk_fields(client):
    r = await client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["disk_free_mb"], int)
    assert isinstance(body["disk_ok"], bool)
    assert body["disk_ok"] == (body["disk_free_mb"] >= get_settings().disk_watermark_mb)
