"""Persona matrix at the route level (spec §9): maintainer boundary,
viewer preflight regression, ops-only, user_admin-only, custom auditor."""
import uuid

from sqlalchemy import select

from graphrag_ui.adapters.models import User
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import (
    ROLE_ID_MAINTAINER,
    ROLE_ID_OPS,
    ROLE_ID_USER_ADMIN,
    ROLE_ID_VIEWER,
)


async def _login(client, email, password):
    r = await client.post("/api/auth/login",
                          json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    hdr = await _login(client, email, initial_pw)
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": initial_pw, "new_password": new_pw})
    return await _login(client, email, new_pw)


async def _admin(client):
    return await _activate(client, "admin@test.local",
                           "admin-pass-123", "admin-new-1")


async def _mk_user(client, admin, email, password):
    r = await client.post("/api/admin/users", headers=admin, json={
        "email": email, "display_name": email.split("@")[0],
        "password": password})
    assert r.status_code == 201
    return await _activate(client, email, password, password + "-2")


async def _user_id(db_session, email) -> uuid.UUID:
    return (await db_session.execute(
        select(User.id).where(User.email == email))).scalar_one()


async def _grant_global(client, admin, db_session, email, role_ids):
    uid = await _user_id(db_session, email)
    r = await client.patch(f"/api/admin/users/{uid}", headers=admin,
                           json={"roles": [str(r) for r in role_ids]})
    assert r.status_code == 200


async def _project(client, owner_hdr, name="P1") -> str:
    r = await client.post("/api/projects", headers=owner_hdr, json={
        "name": name, "input_file_type": "text"})
    assert r.status_code == 201
    return r.json()["id"]


async def _add_member(client, owner_hdr, pid, uid, role_id):
    r = await client.put(f"/api/projects/{pid}/members/{uid}",
                         headers=owner_hdr, json={"role_id": str(role_id)})
    assert r.status_code == 200


async def test_maintainer_full_path(client, app, db_session):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    bob = await _mk_user(client, admin, "bob@test.local", "bob-pass-1234")
    pid = await _project(client, alice)
    await _add_member(client, alice, pid, await _user_id(db_session, "bob@test.local"),
                      ROLE_ID_MAINTAINER)

    # content: allowed
    r = await client.get(f"/api/projects/{pid}/files", headers=bob)
    assert r.status_code == 200
    r = await client.post(f"/api/projects/{pid}/files", headers=bob,
                          files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 201
    # jobs: trigger + preflight (view) + cancel all allowed
    r = await client.get(f"/api/projects/{pid}/jobs/preflight", headers=bob)
    assert r.status_code == 200
    r = await client.post(f"/api/projects/{pid}/jobs", headers=bob,
                          json={"type": "index", "method": "standard"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    # cancel lives at /api/jobs/{id}/cancel — NOT under /api/projects
    r = await client.post(f"/api/jobs/{job_id}/cancel", headers=bob)
    assert r.status_code == 202
    # reads: settings visible
    r = await client.get(f"/api/projects/{pid}/settings", headers=bob)
    assert r.status_code == 200

    # the boundary: settings PUT, dry-run, env keys, project PATCH, members
    r = await client.put(f"/api/projects/{pid}/settings", headers=bob,
                         json={"content": "x", "expected_hash": "h"})
    assert r.status_code == 403
    r = await client.post(f"/api/projects/{pid}/dry-run", headers=bob)
    assert r.status_code == 403
    r = await client.patch(f"/api/projects/{pid}/env", headers=bob,
                           json={"key": "GRAPHRAG_API_KEY", "value": "v"})
    assert r.status_code == 403
    r = await client.patch(f"/api/projects/{pid}", headers=bob,
                           json={"name": "nope"})
    assert r.status_code == 403
    r = await client.put(f"/api/projects/{pid}/members/"
                         f"{await _user_id(db_session, 'alice@test.local')}",
                         headers=bob, json={"role_id": str(ROLE_ID_VIEWER)})
    assert r.status_code == 403


async def test_viewer_preflight_regression(client, app, db_session):
    """Preflight did NOT move to run_jobs (spec decision 6): a viewer
    gets 200 while every write stays 403."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    carol = await _mk_user(client, admin, "carol@test.local", "carol-pass-1")
    pid = await _project(client, alice)
    await _add_member(client, alice, pid,
                      await _user_id(db_session, "carol@test.local"),
                      ROLE_ID_VIEWER)
    assert (await client.get(f"/api/projects/{pid}/jobs/preflight",
                             headers=carol)).status_code == 200
    assert (await client.post(f"/api/projects/{pid}/jobs", headers=carol,
                              json={"type": "index",
                                    "method": "standard"})).status_code == 403
    assert (await client.post(
        f"/api/projects/{pid}/files", headers=carol,
        files={"file": ("a.txt", b"x", "text/plain")})).status_code == 403


async def test_ops_only_sees_and_acts_everywhere_but_not_users(client, app, db_session):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    dave = await _mk_user(client, admin, "dave@test.local", "dave-pass-1")
    await _grant_global(client, admin, db_session, "dave@test.local",
                        [ROLE_ID_OPS])
    pid = await _project(client, alice)

    # ops is not a member but act_any lets him in; view_any lists the project
    projects = (await client.get("/api/projects", headers=dave)).json()
    assert any(p["id"] == pid for p in projects)
    mine = next(p for p in projects if p["id"] == pid)["my_permissions"]
    assert "project:manage" in mine  # my_permissions carries the implication
    r = await client.patch(f"/api/projects/{pid}", headers=dave,
                           json={"description": "by ops"})
    assert r.status_code == 200
    # user management is NOT part of ops
    r = await client.get("/api/admin/users", headers=dave)
    assert r.status_code == 403
    assert r.json()["code"] == "admin_only"


async def test_user_admin_only_manages_users(client, app, db_session):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    erin = await _mk_user(client, admin, "erin@test.local", "erin-pass-1")
    await _grant_global(client, admin, db_session, "erin@test.local",
                        [ROLE_ID_USER_ADMIN])
    pid = await _project(client, alice)

    assert (await client.get("/api/admin/users",
                             headers=erin)).status_code == 200
    # no project visibility beyond own memberships (none)
    assert (await client.get("/api/projects", headers=erin)).json() == []
    assert (await client.get(f"/api/projects/{pid}",
                             headers=erin)).status_code == 403


async def test_custom_auditor_role_via_api(client, app, db_session):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    frank = await _mk_user(client, admin, "frank@test.local", "frank-pass-1")
    role_id = (await client.post("/api/admin/roles", headers=admin, json={
        "scope": "global", "name": "auditor", "description": "",
        "permissions": ["projects:view_any"]})).json()["id"]
    await _grant_global(client, admin, db_session, "frank@test.local",
                        [uuid.UUID(role_id)])
    pid = await _project(client, alice)

    projects = (await client.get("/api/projects", headers=frank)).json()
    assert any(p["id"] == pid for p in projects)
    assert (await client.get(f"/api/projects/{pid}",
                             headers=frank)).status_code == 200
    assert (await client.post(
        f"/api/projects/{pid}/files", headers=frank,
        files={"file": ("a.txt", b"x", "text/plain")})).status_code == 403


async def test_userout_carries_roles_and_permissions(client):
    admin = await _admin(client)  # bootstrap admin = user_admin + ops
    me = (await client.get("/api/auth/me", headers=admin)).json()
    names = {r["name"] for r in me["roles"]}
    assert names == {"user_admin", "ops"}
    assert set(me["permissions"]) == {
        "users:manage", "projects:view_any", "projects:act_any"}


async def test_member_contract_and_my_permissions(client, app, db_session):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _admin(client)
    alice = await _mk_user(client, admin, "alice@test.local", "alice-pass-1")
    pid = await _project(client, alice)
    members = (await client.get(f"/api/projects/{pid}/members",
                                headers=alice)).json()
    assert members[0]["role_name"] == "owner"
    assert members[0]["role_id"] == \
        "00000000-0000-4000-8000-000000000006"
    project = (await client.get(f"/api/projects/{pid}",
                                headers=alice)).json()
    assert set(project["my_permissions"]) == {
        "project:view", "project:edit_content", "project:run_jobs",
        "project:edit_settings", "project:manage"}
