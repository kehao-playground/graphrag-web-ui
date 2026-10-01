"""Blocking work stays off the event loop (R1-73, R2-19): argon2, the
update_output prune and the job log tail each run in a worker thread or are
bounded, so one login or one finished update cannot stall every other
request and the worker's heartbeat."""

import threading
import uuid

from sqlalchemy import update

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import _ERROR_TAIL_CHARS, IndexRunner, log_path_for
from graphrag_ui.adapters.models import Job
from graphrag_ui.services import auth, retention, runner_loop
from tests.test_runner_loop import FakeRunner, _claim, _ok, _seed_job


def _off_loop(threads: list[threading.Thread]) -> bool:
    return bool(threads) and all(t is not threading.main_thread() for t in threads)


async def test_password_hash_and_verify_run_in_a_worker_thread(monkeypatch):
    threads: list[threading.Thread] = []
    real = auth._ph

    class Recording:
        def hash(self, pw):
            threads.append(threading.current_thread())
            return real.hash(pw)

        def verify(self, hashed, pw):
            threads.append(threading.current_thread())
            return real.verify(hashed, pw)

    monkeypatch.setattr(auth, "_ph", Recording())
    hashed = await auth.hash_password("correct horse")
    assert await auth.verify_password("correct horse", hashed)
    assert not await auth.verify_password("wrong", hashed)
    assert len(threads) == 3 and _off_loop(threads)


async def test_the_daily_sweep_prunes_update_output_off_the_loop(app, monkeypatch, tmp_path):
    threads: list[threading.Thread] = []

    def recording(root, keep_latest):
        threads.append(threading.current_thread())
        return 0

    monkeypatch.setattr(retention, "prune_update_output", recording)
    ws = tmp_path / "ws-root"
    (ws / str(uuid.uuid4())).mkdir(parents=True)
    monkeypatch.setattr(retention, "get_settings", _settings_with(ws))
    await retention.sweep_all()
    assert _off_loop(threads)


def _settings_with(workspaces_dir):
    from graphrag_ui.config import get_settings

    real = get_settings()

    def fake():
        return real.model_copy(update={"workspaces_dir": str(workspaces_dir)})

    return fake


async def test_a_finished_update_prunes_off_the_loop(app, monkeypatch):
    threads: list[threading.Thread] = []

    def recording(root, keep_latest):
        threads.append(threading.current_thread())
        return 0

    monkeypatch.setattr(runner_loop, "prune_update_output", recording)
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: FakeRunner(_ok()))
    job_id = await _seed_job()
    async with get_session_factory()() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(type="update"))
        await s.commit()
    await _claim(job_id)
    await runner_loop._execute(job_id)
    assert _off_loop(threads)


async def test_a_failed_job_keeps_the_tail_of_a_long_log(tmp_path):
    """The error is the log's last characters, read from the end of the
    file rather than the whole log; the child writes the log directly."""
    runner = IndexRunner(argv_prefix=("sh", "-c"))
    log = log_path_for(tmp_path, uuid.uuid4())
    lines = 4 * _ERROR_TAIL_CHARS // 10
    res = await runner.run(
        argv=[
            f"i=0; while [ $i -lt {lines} ]; do echo line-$i; i=$((i+1)); done; echo last; exit 2"
        ],
        root=tmp_path,
        log_path=log,
        job_type="index",
        cancel_requested=lambda: False,
    )
    assert res.status == "failed"
    full = log.read_text()
    assert full.startswith("line-0\n") and full.endswith("last\n")
    assert res.error == full[-_ERROR_TAIL_CHARS:].strip()
