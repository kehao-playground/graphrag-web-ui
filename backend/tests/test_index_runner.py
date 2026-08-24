# backend/tests/test_index_runner.py
import asyncio
import json
import uuid

from graphrag_ui.adapters.index_runner import (
    IndexRunner,
    log_path_for,
    read_stats,
)


async def _hb():  # no-op heartbeat; mechanics tested via runner_loop
    return None


async def test_success_captures_log_and_stats(tmp_path):
    root = tmp_path
    # fake "graphrag": sh -c 'echo hello; exit 0'
    r = IndexRunner(argv_prefix=("sh", "-c"))
    log = log_path_for(root, uuid.uuid4())
    res = await r.run(
        argv=["echo hello; exit 0"],
        root=root,
        log_path=log,
        job_type="index",
        heartbeat=_hb,
        cancel_requested=lambda: False,
    )
    assert res.status == "succeeded" and res.exit_code == 0 and res.error is None
    assert b"hello" in log.read_bytes()
    assert res.stats is None  # no stats.json written by the fake

async def test_subprocess_env_silences_litellm_import_warnings(tmp_path):
    # litellm (graphrag's LLM layer) logs "could not pre-load bedrock/sagemaker
    # response stream shape — No module named 'botocore'" at WARNING on every
    # import; its handler level comes from LITELLM_LOG. The job subprocess must
    # carry LITELLM_LOG=ERROR or those two lines top EVERY index job log.
    r = IndexRunner(argv_prefix=("sh", "-c"))
    log = log_path_for(tmp_path, uuid.uuid4())
    res = await r.run(
        argv=["echo LITELLM_LOG=$LITELLM_LOG; exit 0"],
        root=tmp_path,
        log_path=log,
        job_type="index",
        heartbeat=_hb,
        cancel_requested=lambda: False,
    )
    assert res.status == "succeeded"
    assert b"LITELLM_LOG=ERROR" in log.read_bytes()


async def test_subprocess_env_respects_explicit_litellm_log(tmp_path, monkeypatch):
    # an operator debugging LLM calls may set LITELLM_LOG=DEBUG — never stomp it
    monkeypatch.setenv("LITELLM_LOG", "DEBUG")
    r = IndexRunner(argv_prefix=("sh", "-c"))
    log = log_path_for(tmp_path, uuid.uuid4())
    await r.run(
        argv=["echo LITELLM_LOG=$LITELLM_LOG; exit 0"],
        root=tmp_path,
        log_path=log,
        job_type="index",
        heartbeat=_hb,
        cancel_requested=lambda: False,
    )
    assert b"LITELLM_LOG=DEBUG" in log.read_bytes()


async def test_failure_error_is_log_tail(tmp_path):
    r = IndexRunner(argv_prefix=("sh", "-c"))
    log = log_path_for(tmp_path, uuid.uuid4())
    res = await r.run(
        argv=["echo boom >&2; exit 3"],
        root=tmp_path,
        log_path=log,
        job_type="index",
        heartbeat=_hb,
        cancel_requested=lambda: False,
    )
    assert res.status == "failed" and res.exit_code == 3
    assert "boom" in (res.error or "")


async def test_cancel_sigterm_then_cancelled(tmp_path):
    r = IndexRunner(argv_prefix=("sleep",))
    log = log_path_for(tmp_path, uuid.uuid4())
    cancelled = asyncio.Event()
    task = asyncio.create_task(
        r.run(
            argv=["30"],
            root=tmp_path,
            log_path=log,
            job_type="index",
            heartbeat=_hb,
            cancel_requested=cancelled.is_set,
        )
    )
    await asyncio.sleep(0.5)  # let the subprocess start
    cancelled.set()
    res = await asyncio.wait_for(task, timeout=10)
    assert res.status == "cancelled"
    assert res.exit_code is not None and res.exit_code != 0


async def test_read_stats_index_and_update(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "stats.json").write_text(json.dumps({"num_documents": 3}))
    assert (read_stats("index", tmp_path) or {}).get("num_documents") == 3

    uo = tmp_path / "update_output" / "20260821-080000" / "delta"
    uo.mkdir(parents=True)
    (uo / "stats.json").write_text(json.dumps({"update_documents": 1}))
    uo2 = tmp_path / "update_output" / "20260821-090000" / "delta"
    uo2.mkdir(parents=True)
    (uo2 / "stats.json").write_text(json.dumps({"update_documents": 2}))
    # newest timestamp dir wins (lexical sort), output/stats.json NOT read
    assert (read_stats("update", tmp_path) or {}).get("update_documents") == 2


async def test_read_stats_missing_returns_none(tmp_path):
    assert read_stats("index", tmp_path) is None
    assert read_stats("update", tmp_path) is None
