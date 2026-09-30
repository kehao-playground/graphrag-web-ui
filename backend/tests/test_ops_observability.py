"""Fix wave F24: operator-facing logging (R3-11, decision D7) and the audit
scope rule "every state-changing route audits" (R2-23/R3-34, decision D3)."""

import logging

from sqlalchemy import select

from graphrag_ui.adapters.index_runner import RunResult
from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.main import configure_logging
from graphrag_ui.services import retention, runner_loop
from tests.test_jobs_api import _activate, _login, _project, _setup_users
from tests.test_runner_loop import FakeRunner, _claim, _seed_job


async def _audit_rows(db_session, action: str) -> list[AuditLog]:
    res = await db_session.execute(select(AuditLog).where(AuditLog.action == action))
    return list(res.scalars())


# --- audit (decision D3) -----------------------------------------------------


async def test_enqueue_and_cancel_write_audit_rows(client, app, db_session):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    job = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    assert (await client.post(f"/api/jobs/{job['id']}/cancel", headers=alice)).status_code == 202

    [enq] = await _audit_rows(db_session, "job.enqueued")
    assert (enq.target_type, enq.target_id) == ("project", pid)
    assert enq.payload == {"job_id": job["id"], "type": "index", "method": "fast"}
    [cxl] = await _audit_rows(db_session, "job.cancelled")
    assert (cxl.target_type, cxl.target_id) == ("project", pid)
    assert cxl.payload == {"job_id": job["id"], "type": "index"}
    assert enq.actor_id == cxl.actor_id is not None


async def test_refused_enqueue_and_cancel_write_no_audit_row(client, app, db_session):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    body = {"type": "index", "method": "fast"}
    job = (await client.post(f"/api/projects/{pid}/jobs", headers=alice, json=body)).json()
    # a second active job is a 409: its audit row must roll back with it
    assert (
        await client.post(f"/api/projects/{pid}/jobs", headers=alice, json=body)
    ).status_code == 409
    await client.post(f"/api/jobs/{job['id']}/cancel", headers=alice)
    # cancelling a terminal job is a 409 and changes nothing
    assert (await client.post(f"/api/jobs/{job['id']}/cancel", headers=alice)).status_code == 409
    assert len(await _audit_rows(db_session, "job.enqueued")) == 1
    assert len(await _audit_rows(db_session, "job.cancelled")) == 1


async def test_own_password_change_writes_an_audit_row(client, app, db_session):
    await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    [row] = await _audit_rows(db_session, "user.password_changed")
    assert row.target_type == "user" and row.actor_id is not None
    assert row.target_id == str(row.actor_id)


async def test_a_wrong_current_password_writes_no_audit_row(client, app, db_session):
    hdr = await _login(client, "admin@test.local", "admin-pass-123")
    r = await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": "wrong-pass-000", "new_password": "admin-new-1"},
    )
    assert r.status_code == 400
    assert await _audit_rows(db_session, "user.password_changed") == []


# --- logging (R3-11, decision D7) --------------------------------------------


def test_configure_logging_sets_root_info_once():
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        configure_logging()
        ours = [h for h in root.handlers if h.get_name() == "graphrag_ui"]
        assert len(ours) == 1
        assert root.level == logging.INFO
        fmt = ours[0].formatter
        assert fmt is not None
        line = fmt.format(logging.makeLogRecord({"levelname": "INFO", "msg": "m", "name": "n"}))
        # level and timestamp on every line, like uvicorn's own
        assert "INFO" in line and line[:4].isdigit()
        # chatty third-party INFO loggers stay out of the api log
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    finally:
        for h in [h for h in root.handlers if h not in before]:
            root.removeHandler(h)


def test_uvicorn_access_log_redacts_the_sse_token():
    configure_logging()
    access = logging.getLogger("uvicorn.access")
    record = access.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5", "GET", "/api/jobs/j/logs?offset=0&token=eyJSECRET", "1.1", 200),
        None,
    )
    assert all(f.filter(record) for f in access.filters)
    line = record.getMessage()
    assert "eyJSECRET" not in line
    assert "/api/jobs/j/logs?offset=0&token=[redacted]" in line
    # a request without a token is untouched
    plain = access.makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s", ("/api/projects?limit=5",), None
    )
    assert all(f.filter(plain) for f in access.filters)
    assert plain.getMessage() == "/api/projects?limit=5"


def test_uvicorn_access_log_drops_successful_probes():
    """F24-03: healthchecks every 10 s must not bury the job lines."""
    configure_logging()
    access = logging.getLogger("uvicorn.access")
    fmt = '%s - "%s %s HTTP/%s" %d'

    def kept(path: str, status: int) -> bool:
        rec = access.makeRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            fmt,
            ("127.0.0.1:5", "GET", path, "1.1", status),
            None,
        )
        return all(f.filter(rec) for f in access.filters)

    assert not kept("/api/health", 200)
    assert not kept("/api/ready", 200)
    # a failing probe is exactly what an operator wants to see
    assert kept("/api/ready", 503)
    assert kept("/api/healthz-not-a-probe", 200)
    assert kept("/api/projects/health", 200)


async def test_job_lifecycle_is_logged(client, app, caplog):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    with caplog.at_level(logging.INFO):
        job = (
            await client.post(
                f"/api/projects/{pid}/jobs",
                headers=alice,
                json={"type": "index", "method": "fast"},
            )
        ).json()
        await client.post(f"/api/jobs/{job['id']}/cancel", headers=alice)
    text = caplog.text
    assert f"job enqueued id={job['id']}" in text and "type=index" in text
    assert f"job cancel requested id={job['id']}" in text


async def test_execute_logs_start_and_finish_with_duration(app, monkeypatch, caplog):
    fake = FakeRunner(RunResult(status="failed", exit_code=137, error="x", stats=None))
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: fake)
    job_id = await _seed_job()
    await _claim(job_id)
    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        await runner_loop._execute(job_id)
    assert f"job started id={job_id} type=index" in caplog.text
    finished = [r.getMessage() for r in caplog.records if "job finished" in r.getMessage()]
    assert len(finished) == 1
    assert f"id={job_id}" in finished[0] and "status=failed exit=137" in finished[0]
    assert "duration=" in finished[0]


async def test_loop_logs_the_claim(app, monkeypatch, caplog):
    import asyncio

    from graphrag_ui.config import get_settings

    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    get_settings.cache_clear()
    ok = RunResult(status="succeeded", exit_code=0, error=None, stats=None)
    monkeypatch.setattr(runner_loop, "IndexRunner", lambda: FakeRunner(ok))
    job_id = await _seed_job()
    stop = asyncio.Event()
    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        task = asyncio.create_task(runner_loop.run_loop(stop))
        for _ in range(50):
            if f"job claimed id={job_id}" in caplog.text:
                break
            await asyncio.sleep(0.1)
        stop.set()
        await task
        await asyncio.gather(*runner_loop._executing)
    assert f"job claimed id={job_id}" in caplog.text


async def test_reconcile_logs_what_it_finished(app, caplog):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from graphrag_ui.adapters.db import get_session_factory
    from graphrag_ui.adapters.models import Job

    job_id = await _seed_job()
    await _claim(job_id)
    async with get_session_factory()() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await s.commit()
    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        assert await runner_loop.reconcile_stale() == 1
    assert f"job {job_id} interrupted" in caplog.text
    assert "reconciled 1 stale job" in caplog.text


async def test_quiet_reconcile_logs_nothing(app, caplog):
    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        assert await runner_loop.reconcile_stale() == 0
    assert "reconciled" not in caplog.text


async def test_retention_sweep_logs_what_it_reclaimed(app, caplog):
    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        await retention.sweep_all()
    assert "retention sweep: deleted 0 job logs, 0 start snapshots; pruned 0" in caplog.text


async def test_bootstrap_admin_creation_is_logged(app, caplog):
    # `app` alone runs no lifespan, so no admin exists yet
    from graphrag_ui.adapters.db import get_session_factory
    from graphrag_ui.services.auth import bootstrap_admin

    with caplog.at_level(logging.INFO, logger="graphrag_ui"):
        async with get_session_factory()() as s:
            await bootstrap_admin(s)
    assert "bootstrap admin admin@test.local created" in caplog.text
