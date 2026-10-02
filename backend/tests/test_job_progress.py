"""Index/update workflow progress (R3-36): graphrag rewrites stats.json after
every workflow, and the runner's watch loop turns it into jobs.progress."""

import asyncio
import json
import os
import time

import pytest
from graphrag.index.workflows.factory import PipelineFactory

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.adapters.index_runner import RunResult, configured_workflows, read_stats
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import PIPELINE_WORKFLOWS, workflow_progress, workflow_total
from graphrag_ui.services import runner_loop
from graphrag_ui.services.projects import ws_path
from tests.test_runner_loop import _running_job


def test_pipeline_lists_match_the_pinned_graphrag():
    # graphrag appends "-update" to the method for update jobs (spec §6.3).
    for (job_type, method), workflows in PIPELINE_WORKFLOWS.items():
        name = method if job_type == "index" else f"{method}-update"
        assert list(workflows) == PipelineFactory.pipelines[name], (job_type, method)


def test_workflow_total_by_job_type_method_and_override():
    assert workflow_total("index", "standard", None) == 10
    assert workflow_total("index", "fast", None) == 10
    assert workflow_total("update", "standard", None) == 18
    # settings.yaml `workflows:` replaces the built-in pipeline outright.
    assert workflow_total("index", "standard", ["a", "b", "c"]) == 3
    # anything that is not a non-empty list of names is ignored
    assert workflow_total("index", "standard", []) == 10
    assert workflow_total("index", "standard", "extract_graph") == 10


def test_workflow_progress_counts_finished_workflows_and_caps_at_total():
    assert workflow_progress(None, 10) == {"done": 0, "total": 10}
    assert workflow_progress({"workflows": {}}, 10) == {"done": 0, "total": 10}
    stats = {"workflows": {"load_input_documents": {}, "create_base_text_units": {}}}
    assert workflow_progress(stats, 10) == {"done": 2, "total": 10}
    assert workflow_progress({"workflows": {str(i): {} for i in range(12)}}, 10) == {
        "done": 10,
        "total": 10,
    }
    assert workflow_progress({"workflows": "garbage"}, 10) == {"done": 0, "total": 10}


def test_read_stats_ignores_a_file_older_than_since(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    f = out / "stats.json"
    f.write_text(json.dumps({"num_documents": 3}))
    past = time.time() - 60
    os.utime(f, (past, past))
    # The previous index's stats are not this run's.
    assert read_stats("index", tmp_path, since=time.time() - 5) is None
    assert (read_stats("index", tmp_path, since=past - 1) or {}).get("num_documents") == 3

    old = tmp_path / "update_output" / "20260821-080000" / "delta"
    old.mkdir(parents=True)
    (old / "stats.json").write_text("{}")
    os.utime(old / "stats.json", (past, past))
    assert read_stats("update", tmp_path, since=time.time() - 5) is None


def test_configured_workflows_reads_the_settings_key(tmp_path):
    assert configured_workflows(tmp_path) is None  # no settings.yaml
    (tmp_path / "settings.yaml").write_text("models: {}\n")
    assert configured_workflows(tmp_path) is None
    (tmp_path / "settings.yaml").write_text("workflows: [a, b]\napi_key: ${KEY}\n")
    assert configured_workflows(tmp_path) == ["a", "b"]
    (tmp_path / "settings.yaml").write_text("{unbalanced")
    assert configured_workflows(tmp_path) is None  # best-effort, never raises


async def _progress_when(progress_now, expected: dict, timeout_s: float = 10.0):
    """Poll until the row shows `expected`; return what it shows at that
    point or at the deadline. Waiting on the value itself instead of a
    fixed sleep keeps a slow host from reading the row before the watch
    loop's next beat has written it."""
    deadline = time.monotonic() + timeout_s
    while True:
        progress = await progress_now()
        if progress == expected or time.monotonic() >= deadline:
            return progress
        await asyncio.sleep(0.02)


@pytest.fixture
async def _workspaces(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    get_settings.cache_clear()
    await reset_engine()
    monkeypatch.setattr(runner_loop, "_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(runner_loop, "_CANCEL_POLL_S", 0.05)


async def test_watch_loop_ticks_workflow_progress_from_stats_json(
    db_session, monkeypatch, _workspaces
):
    job = await _running_job(db_session, type_="index")
    root = ws_path(job.project_id)
    (root / "output").mkdir(parents=True)
    # A previous index's stats.json must not count as this run's progress.
    stale = root / "output" / "stats.json"
    stale.write_text(json.dumps({"workflows": {str(i): {} for i in range(10)}}))
    past = time.time() - 600
    os.utime(stale, (past, past))
    seen: list[dict | None] = []

    async def progress_now():
        await db_session.refresh(job)
        return job.progress

    class StepRunner:
        async def run(self, **kwargs):
            seen.append(await _progress_when(progress_now, {"done": 0, "total": 10}))
            stats = {"workflows": {"load_input_documents": {}, "create_base_text_units": {}}}
            stale.write_text(json.dumps(stats))
            seen.append(await _progress_when(progress_now, {"done": 2, "total": 10}))
            return RunResult(status="succeeded", exit_code=0, error=None, stats=None)

    monkeypatch.setattr(runner_loop, "IndexRunner", StepRunner)
    await runner_loop._execute(job.id)

    assert seen == [{"done": 0, "total": 10}, {"done": 2, "total": 10}]


async def test_progress_total_follows_a_settings_workflows_override(
    db_session, monkeypatch, _workspaces
):
    job = await _running_job(db_session, type_="index")
    root = ws_path(job.project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.yaml").write_text("workflows: [a, b, c]\n")

    async def progress_now():
        await db_session.refresh(job)
        return job.progress

    class SlowRunner:
        async def run(self, **kwargs):
            await _progress_when(progress_now, {"done": 0, "total": 3})
            return RunResult(status="succeeded", exit_code=0, error=None, stats=None)

    monkeypatch.setattr(runner_loop, "IndexRunner", SlowRunner)
    await runner_loop._execute(job.id)
    await db_session.refresh(job)
    assert job.progress == {"done": 0, "total": 3}


async def test_a_beat_replaces_that_seconds_cancel_poll(db_session, monkeypatch, _workspaces):
    """R1-99: while every iteration beats, the cancel flag arrives with the
    heartbeat's RETURNING and the separate poll never runs."""
    job = await _running_job(db_session, type_="index")
    polls = {"n": 0}
    real_poll = runner_loop._cancel_requested_in_db

    async def counting(job_id):
        polls["n"] += 1
        return await real_poll(job_id)

    monkeypatch.setattr(runner_loop, "_cancel_requested_in_db", counting)
    monkeypatch.setattr(runner_loop, "_HEARTBEAT_S", 0.0)

    class CancelledRunner:
        async def run(self, *, cancel_requested, **kwargs):
            from graphrag_ui.adapters import jobs_repo

            await jobs_repo.request_cancel(db_session, job.id)
            await db_session.commit()
            for _ in range(100):
                if cancel_requested():
                    return RunResult(status="cancelled", exit_code=-15, error=None, stats=None)
                await asyncio.sleep(0.02)
            return RunResult(status="succeeded", exit_code=0, error=None, stats=None)

    monkeypatch.setattr(runner_loop, "IndexRunner", CancelledRunner)
    await runner_loop._execute(job.id)
    await db_session.refresh(job)
    assert job.status == "cancelled"
    assert polls["n"] == 0
