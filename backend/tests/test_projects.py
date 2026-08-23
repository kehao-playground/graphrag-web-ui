import pytest
import yaml

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """Every new account (incl. the bootstrap admin) has must_change_password=True — usable only after the change."""
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
    assert (ws / "settings.yaml").exists()      # graphrag init really ran
    assert (ws / "input").exists()
    cfg = yaml.safe_load((ws / "settings.yaml").read_text())
    assert cfg["input"]["type"] == "text"  # never assert `"text" in yaml_text`:
    #   settings.yaml already contains strings like text-embedding-3-large, so
    #   that weaker form would pass even without the patch
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
    # alice adds bob as viewer -> he can read but not modify
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 200
    assert (await client.patch(f"/api/projects/{pid}", headers=bob,
                               json={"name": "X"})).status_code == 403
    # bob cannot manage members
    assert (await client.delete(f"/api/projects/{pid}/members/{bob_id}",
                                headers=bob)).status_code == 403
    # Non-owners cannot delete the project; the owner can
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


async def test_delete_project_cascades_members(client, app, db_session):
    # Regression: deleting a project must clear project_members via FK CASCADE.
    app.dependency_overrides[get_initializer] = FakeInitializer
    from sqlalchemy import select

    from graphrag_ui.adapters.models import ProjectMember
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "Cascade", "input_file_type": "text"})).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    assert (await db_session.execute(
        select(ProjectMember).where(ProjectMember.project_id == pid))).scalars().all(), \
        "precondition: members exist"
    db_session.expire_all()  # detach cache so cascade is observed fresh
    await client.delete(f"/api/projects/{pid}", headers=alice)
    rows = (await db_session.execute(
        select(ProjectMember).where(ProjectMember.project_id == pid))).scalars().all()
    assert rows == []


async def test_init_failure_leaves_no_row(client, app):
    # Regression: graphrag init failure must roll back the project row.
    from graphrag_ui.adapters.workspace import WorkspaceInitError

    class ExplodingInitializer:
        async def init(self, root, input_file_type):
            raise WorkspaceInitError("simulated graphrag init failure")

    app.dependency_overrides[get_initializer] = lambda: ExplodingInitializer()
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post("/api/projects", headers=alice, json={
        "name": "Exploder", "input_file_type": "text"})
    assert r.status_code == 500
    assert r.json() == {"detail": "graphrag init failed"}
    names = [p["name"] for p in (await client.get("/api/projects", headers=alice)).json()]
    assert "Exploder" not in names  # rollback left no residual row


async def test_owner_role_not_grantable(client, app):
    # Single-owner policy: owner is fixed to the creator and not grantable via API.
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "Solo", "input_file_type": "text"})).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    r = await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                         json={"role": "owner"})
    assert r.status_code == 422  # owner is fixed to the creator (single-owner policy)
