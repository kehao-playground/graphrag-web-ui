"""Task 3: jobs REST endpoints — enqueue/list/detail/cancel/preflight.

Auth/member-management helpers copied from test_projects.py (exact verb/shape:
bootstrap admin activation, admin-created users, owner PUT member)."""

import uuid

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.config import get_settings
from graphrag_ui.services.projects import ws_path


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """所有新帳號(含 bootstrap admin)must_change_password=True — 換完密碼才可用。"""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _setup_users(client, app):
    """admin + alice(專案 owner)+ bob(稍後被加為 viewer)。回傳三組 headers。"""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    await client.post(
        "/api/admin/users",
        headers=admin,
        json={"email": "alice@test.local", "display_name": "Alice", "password": "alice-pass-1"},
    )
    await client.post(
        "/api/admin/users",
        headers=admin,
        json={"email": "bob@test.local", "display_name": "Bob", "password": "bob-pass-1234"},
    )
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    return admin, alice, bob


async def _project(client, alice, name="P3"):
    r = await client.post(
        "/api/projects",
        headers=alice,
        json={"name": name, "description": None, "input_file_type": "text"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_enqueue_and_list(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    r = await client.post(
        f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["display_status"] == "queued"
    assert job["argv"] == ["index", "--root", str(ws_path(uuid.UUID(pid))), "--method", "fast"]
    lst = await client.get(f"/api/projects/{pid}/jobs", headers=alice)
    assert [j["id"] for j in lst.json()] == [job["id"]]


async def test_second_active_job_409(client, app):
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
    assert r.status_code == 409 and "進行中" in r.json()["detail"]


async def test_disk_watermark_409(client, app, monkeypatch):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    # 把水位調到高於任何真實磁碟 → enqueue 必須擋下(spec §6.1 pre-check)
    monkeypatch.setenv("DISK_WATERMARK_MB", "99999999")
    get_settings.cache_clear()
    r = await client.post(
        f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
    )
    assert r.status_code == 409 and "磁碟" in r.json()["detail"]


async def test_invalid_type_method_422(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    r = await client.post(
        f"/api/projects/{pid}/jobs", headers=alice, json={"type": "reindex", "method": "fast"}
    )
    assert r.status_code == 422


async def test_viewer_cannot_start_but_can_read(client, app):
    _, alice, bob = await _setup_users(client, app)
    pid = await _project(client, alice)
    # owner 從窄清單解析 email → user_id 後把 bob 加為 viewer(同 test_projects.py)
    users = (await client.get("/api/users", headers=alice)).json()
    vid = next(u["id"] for u in users if u["email"] == "bob@test.local")
    r = await client.put(
        f"/api/projects/{pid}/members/{vid}", headers=alice, json={"role": "viewer"}
    )
    assert r.status_code in (200, 201)
    # viewer: read OK, start 403, cancel 403
    assert (await client.get(f"/api/projects/{pid}/jobs", headers=bob)).status_code == 200
    assert (await client.get(f"/api/projects/{pid}/jobs/preflight", headers=bob)).status_code == 200
    assert (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=bob, json={"type": "index", "method": "fast"}
        )
    ).status_code == 403
    j = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    assert (await client.post(f"/api/jobs/{j['id']}/cancel", headers=bob)).status_code == 403
    assert (await client.get(f"/api/jobs/{j['id']}", headers=bob)).status_code == 200


async def test_cancel_flow(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    j = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    r = await client.post(f"/api/jobs/{j['id']}/cancel", headers=alice)
    assert r.status_code == 202 and r.json()["detail"] == "已請求取消"
    got = (await client.get(f"/api/jobs/{j['id']}", headers=alice)).json()
    assert got["cancel_requested_at"] is not None
    # cancelling a terminal job
    from graphrag_ui.adapters.db import get_session_factory
    from graphrag_ui.adapters.jobs_repo import claim_next, finish

    async with get_session_factory()() as s:
        await claim_next(s, "w-test")
        await finish(s, uuid.UUID(j["id"]), "cancelled", exit_code=-15)
    r2 = await client.post(f"/api/jobs/{j['id']}/cancel", headers=alice)
    assert r2.status_code == 409


async def test_preflight_shape(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    r = await client.get(f"/api/projects/{pid}/jobs/preflight", headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert body["active_job"] is None and body["last_run"] is None
    assert body["cache_quota_mb"] > 0 and body["disk_watermark_mb"] > 0
    assert isinstance(body["cache_bytes"], int) and body["cache_bytes"] == 0
    assert body["disk_free_mb"] > 0


async def test_preflight_active_and_last_run(client, app):
    _, alice, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    j = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=alice, json={"type": "index", "method": "fast"}
        )
    ).json()
    # queued job → active_job 帶完整 JobOut 形狀
    pre = (await client.get(f"/api/projects/{pid}/jobs/preflight", headers=alice)).json()
    assert pre["active_job"]["id"] == j["id"]
    assert pre["active_job"]["status"] == "queued"
    # 跑完(stats 由 runner 寫入;此處直接以 repo 模擬)→ active_job 消失、last_run 出現
    from graphrag_ui.adapters.db import get_session_factory
    from graphrag_ui.adapters.jobs_repo import claim_next, finish

    async with get_session_factory()() as s:
        await claim_next(s, "w-test")
        await finish(
            s,
            uuid.UUID(j["id"]),
            "succeeded",
            exit_code=0,
            stats={"total_runtime": 12.5, "num_documents": 7, "update_documents": 2},
        )
    pre2 = (await client.get(f"/api/projects/{pid}/jobs/preflight", headers=alice)).json()
    assert pre2["active_job"] is None
    assert pre2["last_run"]["type"] == "index"
    assert pre2["last_run"]["status"] == "succeeded"
    assert pre2["last_run"]["finished_at"] is not None
    assert pre2["last_run"]["total_runtime_seconds"] == 12.5
    assert pre2["last_run"]["num_documents"] == 7
    assert pre2["last_run"]["update_documents"] == 2
