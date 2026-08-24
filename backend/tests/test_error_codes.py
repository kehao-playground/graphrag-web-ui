"""Route → error code assertions (i18n spec §4.2), one place for all
routes. detail strings are pinned elsewhere; here we pin the code field.
Query-route entries re-wire the test_query_api seams locally (config stub,
fake adapter/cache) so this file stays lint-clean without fixture imports."""

import uuid
from types import SimpleNamespace

import pytest
from test_files import _alice, _make_project
from test_jobs_api import _project, _setup_users
from test_query_api import FakeAdapter, FakeCache, _post, _viewer_setup

from graphrag_ui.config import get_settings
from graphrag_ui.services import query as query_service
from graphrag_ui.services.rate_limit import reset_rate_limiter


@pytest.fixture
def _seams(monkeypatch):
    """Same contract as test_query_api._seams: stub config load, keep the
    settings/limiter singletons from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    reset_rate_limiter()
    yield
    get_settings.cache_clear()
    reset_rate_limiter()


@pytest.fixture
def fake_adapter(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
    return adapter


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(query_service, "get_frame_cache", lambda: cache)
    return cache


async def test_login_failure_carries_code(client):
    r = await client.post("/api/auth/login",
                          json={"email": "admin@test.local", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth_invalid_credentials"
    assert r.json()["detail"] == "invalid email or password"


async def test_must_change_guard_carries_code(client):
    # Fresh bootstrap admin starts must_change_password=True; the global
    # guard 403s any /api path outside the allowlist (main.py §4.4 exit).
    # The guard only fires when Authorization starts with "Bearer " —
    # a bare request would 401 in get_current_user instead.
    login = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.local", "password": "admin-pass-123"})
    token = login.json()["access_token"]
    r = await client.get("/api/projects",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    body = r.json()
    assert body["detail"] == "password change required"
    assert body["code"] == "auth_must_change_password"


async def test_query_not_indexed_carries_code(client, app, _seams, fake_adapter):
    # real frame cache + empty workspace: no output/*.parquet
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 409
    assert r.json()["code"] == "not_indexed"


async def test_query_config_failed_carries_code(client, app, _seams, fake_adapter,
                                                fake_cache, monkeypatch):
    from graphrag_ui.adapters.graphrag_search import ConfigLoadError

    def boom(root):
        raise ConfigLoadError("bad settings.yaml: secret missing")

    monkeypatch.setattr(query_service, "load_config", boom)
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 500
    assert r.json()["code"] == "query_config_failed"


async def test_query_failed_carries_code(client, app, _seams, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    fake_adapter.error = RuntimeError("OPENAI key invalid: sk-leaked-123")
    r = await _post(client, pid, alice)
    assert r.status_code == 502
    assert r.json()["code"] == "query_failed"


async def test_query_rate_limited_carries_code(client, app, _seams, fake_adapter,
                                               fake_cache, monkeypatch):
    monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    reset_rate_limiter()
    pid, alice, _, _ = await _viewer_setup(client, app)
    assert (await _post(client, pid, alice)).status_code == 200
    assert (await _post(client, pid, alice)).status_code == 200
    r = await _post(client, pid, alice)
    assert r.status_code == 429
    assert r.json()["code"] == "query_rate_limited"


async def test_upload_bad_extension_carries_code_and_params(client):
    # Same mint as test_files.py: activated alice + real-API project (real
    # graphrag init) so validation runs against the real workspace layout.
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    files = {"file": ("notes.exe", b"x")}
    r = await client.post(f"/api/projects/{pid}/files", files=files,
                          headers=alice)
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "file_ext_not_allowed"
    assert body["params"] == {"ext": ".exe", "input_file_type": "text"}
    assert body["detail"].startswith("extension '.exe' not allowed")


async def test_job_not_found_carries_code(client, app):
    _, alice, _ = await _setup_users(client, app)
    r = await client.get(f"/api/jobs/{uuid.uuid4()}", headers=alice)
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "job not found"
    assert body["code"] == "job_not_found"


async def test_job_conflict_carries_code(client, app):
    # Same mint as test_jobs_api.test_second_active_job_409: a queued job
    # blocks the second enqueue.
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    assert (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).status_code == 201
    r = await client.post(
        f"/api/projects/{pid}/jobs", headers=alice, json={"type": "update", "method": "standard"}
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "此專案已有進行中的索引任務"
    assert body["code"] == "job_conflict"


async def test_disk_watermark_carries_code(client, app, monkeypatch):
    # Same mint as test_jobs_api.test_disk_watermark_409: raise the
    # watermark above any real disk. NO params — the UI numbers come from
    # the preflight endpoint (spec §4.2).
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    monkeypatch.setenv("DISK_WATERMARK_MB", "99999999")
    get_settings.cache_clear()
    r = await client.post(
        f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "磁碟剩餘空間不足"
    assert body["code"] == "disk_watermark"
    assert "params" not in body


async def test_job_already_finished_carries_code(client, app):
    # Same mint as test_jobs_api.test_cancel_flow: finish the job via the
    # repo directly, then cancel must 409.
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    j = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    from graphrag_ui.adapters.db import get_session_factory
    from graphrag_ui.adapters.jobs_repo import claim_next, finish

    async with get_session_factory()() as s:
        await claim_next(s, "w-test")
        await finish(s, uuid.UUID(j["id"]), "cancelled", exit_code=-15)
    r = await client.post(f"/api/jobs/{j['id']}/cancel", headers=alice)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "任務已結束"
    assert body["code"] == "job_already_finished"


async def test_job_invalid_last_event_id_carries_code(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    j = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    r = await client.get(f"/api/jobs/{j['id']}/logs",
                         headers={**alice, "Last-Event-ID": "not-a-number"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "invalid Last-Event-ID"
    assert body["code"] == "job_invalid_last_event_id"


@pytest.fixture
async def project_with_members(client, app):
    """Project with owner + a second member, mirroring the members
    fixtures in test_projects.py. admin_headers belongs to the site
    admin, who may manage members on any project."""
    admin, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    members = (await client.get(f"/api/projects/{pid}/members",
                                headers=alice)).json()
    owner_id = next(m["user_id"] for m in members if m["role"] == "owner")
    return SimpleNamespace(id=pid, owner_id=owner_id, admin_headers=admin)


async def test_demote_owner_carries_code(client, app, project_with_members):
    # project_with_members: owner + a second admin, mirrors the members
    # fixtures in test_projects.py.
    r = await client.put(
        f"/api/projects/{project_with_members.id}/members/{project_with_members.owner_id}",
        json={"role": "editor"}, headers=project_with_members.admin_headers)
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "cannot demote or remove the project owner"
    assert body["code"] == "member_owner_protected"
