import pytest
import yaml

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """所有新帳號(含 bootstrap admin)must_change_password=True — 換完密碼才可用。"""
    hdr = await _login(client, email, initial_pw)
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": initial_pw, "new_password": new_pw})
    return await _login(client, email, new_pw)


async def _setup_two_users(client):
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    await client.post("/api/admin/users", headers=admin, json={
        "email": "alice@test.local", "display_name": "Alice", "password": "alice-pass-1"})
    await client.post("/api/admin/users", headers=admin, json={
        "email": "bob@test.local", "display_name": "Bob", "password": "bob-pass-1234"})
    return admin


@pytest.mark.slow
async def test_create_project_runs_init_and_adds_owner(client, tmp_path):
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post("/api/projects", headers=alice, json={
        "name": "Research Corpus", "input_file_type": "text"})
    assert r.status_code == 201
    pid = r.json()["id"]
    ws = tmp_path / "ws" / pid
    assert (ws / "settings.yaml").exists()      # graphrag init 真的跑過
    assert (ws / "input").exists()
    cfg = yaml.safe_load((ws / "settings.yaml").read_text())
    assert cfg["input"]["type"] == "text"  # 不可寫 `"text" in yaml_text`:
    #   settings.yaml 本來就有 text-embedding-3-large 等字串,那樣寫就算沒 patch 也會過
    members = (await client.get(f"/api/projects/{pid}/members", headers=alice)).json()
    assert members[0]["email"] == "alice@test.local" and members[0]["role"] == "owner"


async def test_permission_matrix_enforced(client, app):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "P1", "input_file_type": "text"})).json()["id"]
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 403
    # alice 加 bob 為 viewer → 可讀不可改
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 200
    assert (await client.patch(f"/api/projects/{pid}", headers=bob,
                               json={"name": "X"})).status_code == 403
    # bob 不能管理成員
    assert (await client.delete(f"/api/projects/{pid}/members/{bob_id}",
                                headers=bob)).status_code == 403
    # 非 owner 不能刪專案;owner 可以
    assert (await client.delete(f"/api/projects/{pid}", headers=bob)).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}", headers=alice)).status_code == 204


async def test_delete_project_removes_workspace(client, app, tmp_path):
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "P2", "input_file_type": "csv"})).json()["id"]
    assert (tmp_path / "ws" / pid).exists()
    await client.delete(f"/api/projects/{pid}", headers=alice)
    assert not (tmp_path / "ws" / pid).exists()
