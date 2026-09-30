"""Fix wave F25, R3-25: spec §10's resource governance at job launch — the
project quota is checked at enqueue, and the cache cap has a way to act
(POST /projects/{pid}/cache:clear)."""

import uuid

from sqlalchemy import select

from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.config import get_settings
from graphrag_ui.services.projects import ws_path
from tests.test_jobs_api import _project, _setup_users

_MIB = 1024 * 1024
_INDEX = {"type": "index", "method": "fast"}


def _fill(pid: str, sub: str, mib: int) -> None:
    d = ws_path(uuid.UUID(pid)) / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob.bin").write_bytes(b"x" * (mib * _MIB))


async def test_preflight_reports_usage_and_quota(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    _fill(pid, "output", 1)
    body = (await client.get(f"/api/projects/{pid}/jobs/preflight", headers=alice)).json()
    assert body["usage_bytes"] >= _MIB
    assert body["project_quota_mb"] == get_settings().project_quota_mb


async def test_enqueue_refuses_a_project_over_its_quota(client, app, monkeypatch):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    monkeypatch.setenv("PROJECT_QUOTA_MB", "1")
    get_settings.cache_clear()
    _fill(pid, "output", 2)
    r = await client.post(f"/api/projects/{pid}/jobs", headers=alice, json=_INDEX)
    # 409, not the upload path's 413: the request itself is not too large
    assert r.status_code == 409
    assert r.json()["code"] == "quota_exceeded"
    assert r.json()["params"] == {"quota_mb": 1}
    lst = (await client.get(f"/api/projects/{pid}/jobs", headers=alice)).json()
    assert lst["total"] == 0


async def test_cache_clear_frees_the_cache_and_audits(client, app, db_session):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    _fill(pid, "cache", 1)
    (ws_path(uuid.UUID(pid)) / "cache" / "nested").mkdir()
    r = await client.post(f"/api/projects/{pid}/cache:clear", headers=alice)
    assert r.status_code == 200, r.text
    assert r.json() == {"freed_bytes": _MIB}
    cache = ws_path(uuid.UUID(pid)) / "cache"
    # emptied, not removed: graphrag and the preflight both expect the dir
    assert cache.is_dir() and list(cache.iterdir()) == []
    pre = (await client.get(f"/api/projects/{pid}/jobs/preflight", headers=alice)).json()
    assert pre["cache_bytes"] == 0
    [row] = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "cache.cleared"))
    ).scalars()
    assert (row.target_type, row.target_id) == ("project", pid)
    assert row.payload == {"freed_bytes": _MIB}


async def test_cache_clear_is_refused_while_a_job_is_active(client, app, db_session):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    _fill(pid, "cache", 1)
    assert (
        await client.post(f"/api/projects/{pid}/jobs", headers=alice, json=_INDEX)
    ).status_code == 201
    r = await client.post(f"/api/projects/{pid}/cache:clear", headers=alice)
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"
    assert (ws_path(uuid.UUID(pid)) / "cache" / "blob.bin").is_file()
    rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "cache.cleared"))
    ).scalars()
    assert list(rows) == []


async def test_cache_clear_on_a_project_without_a_cache(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    r = await client.post(f"/api/projects/{pid}/cache:clear", headers=alice)
    assert r.status_code == 200 and r.json() == {"freed_bytes": 0}


async def test_cache_clear_needs_run_jobs(client, app):
    _, alice, bob = await _setup_users(client, app)
    pid = await _project(client, alice)
    # bob is not a member of alice's project
    r = await client.post(f"/api/projects/{pid}/cache:clear", headers=bob)
    assert r.status_code in (403, 404)
