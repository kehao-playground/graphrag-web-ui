"""GET /api/roles + /api/admin/roles CRUD (spec §7)."""
import uuid

from sqlalchemy import select

from graphrag_ui.adapters.models import Project, ProjectMember, User
from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER


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


async def test_catalog_open_to_authenticated_users(client):
    admin = await _admin(client)
    r = await client.get("/api/roles", headers=admin)
    assert r.status_code == 200
    names = {role["name"] for role in r.json()}
    assert names == {"user_admin", "ops", "viewer", "maintainer",
                     "editor", "owner"}
    r = await client.get("/api/roles?scope=project", headers=admin)
    assert {role["name"] for role in r.json()} == {
        "viewer", "maintainer", "editor", "owner"}


async def test_catalog_requires_authentication(client):
    assert (await client.get("/api/roles")).status_code == 401


async def test_admin_crud_and_audit(client, db_session):
    from graphrag_ui.adapters.models import AuditLog
    admin = await _admin(client)
    body = {"scope": "global", "name": "auditor",
            "description": "read everything",
            "permissions": ["projects:view_any"]}
    r = await client.post("/api/admin/roles", headers=admin, json=body)
    assert r.status_code == 201
    role = r.json()
    assert role["is_system"] is False
    assert role["permissions"] == ["projects:view_any"]
    role_id = role["id"]

    actions = (await db_session.execute(
        select(AuditLog.action).where(AuditLog.action == "role.created")
    )).scalars().all()
    assert actions

    r = await client.patch(f"/api/admin/roles/{role_id}", headers=admin,
                           json={"name": "auditor", "description": "x",
                                 "permissions": []})
    assert r.status_code == 200 and r.json()["permissions"] == []

    r = await client.delete(f"/api/admin/roles/{role_id}", headers=admin)
    assert r.status_code == 204


async def test_admin_list_carries_usage_counts(client):
    admin = await _admin(client)
    # Since the RBAC cutover the bootstrap admin really holds the
    # composition [user_admin, ops] — no manual grant needed here.
    r = await client.get("/api/admin/roles", headers=admin)
    counts = {role["name"]: (role["user_count"], role["member_count"])
              for role in r.json()}
    assert counts["user_admin"] == counts["ops"] == (1, 0)
    assert counts["viewer"] == (0, 0)


async def test_wrong_scope_atom_rejected(client):
    admin = await _admin(client)
    r = await client.post("/api/admin/roles", headers=admin, json={
        "scope": "global", "name": "bad", "description": "",
        "permissions": ["project:view"]})
    assert r.status_code == 400
    assert r.json()["code"] == "role_permissions_invalid"


async def test_system_role_immutable_via_api(client):
    admin = await _admin(client)
    r = await client.patch(
        f"/api/admin/roles/{ROLE_ID_VIEWER}", headers=admin,
        json={"name": "viewer", "description": "", "permissions": []})
    assert r.status_code == 400
    assert r.json()["code"] == "role_is_system"
    r = await client.delete(f"/api/admin/roles/{ROLE_ID_VIEWER}",
                            headers=admin)
    assert r.status_code == 400
    assert r.json()["code"] == "role_is_system"


async def test_delete_in_use_conflicts(client, db_session):
    admin = await _admin(client)
    role_id = (await client.post("/api/admin/roles", headers=admin, json={
        "scope": "project", "name": "aud", "description": "",
        "permissions": ["project:view"]})).json()["id"]
    # make it in-use with a direct member row
    admin_row = (await db_session.execute(
        select(User).where(User.email == "admin@test.local"))).scalar_one()
    project = Project(name="P", slug=f"p-{uuid.uuid4().hex[:8]}",
                      owner_id=admin_row.id, input_file_type="text")
    db_session.add(project)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id,
                                 user_id=admin_row.id,
                                 role_id=uuid.UUID(role_id)))
    await db_session.commit()
    r = await client.delete(f"/api/admin/roles/{role_id}", headers=admin)
    assert r.status_code == 409
    assert r.json()["code"] == "role_in_use"


async def test_non_admin_crud_forbidden(client):
    admin = await _admin(client)
    await client.post("/api/admin/users", headers=admin, json={
        "email": "alice@test.local", "display_name": "A",
        "password": "alice-pass-1"})
    alice = await _activate(client, "alice@test.local",
                            "alice-pass-1", "alice-pass-2")
    r = await client.post("/api/admin/roles", headers=alice, json={
        "scope": "global", "name": "x", "description": "",
        "permissions": []})
    assert r.status_code == 403
    assert r.json()["code"] == "admin_only"


async def test_patch_concurrent_rename_maps_integrity_error(client, monkeypatch):
    # A concurrent rename slipping past the service's check-then-update
    # surfaces as IntegrityError from the uq_roles_scope_name index. The
    # real race cannot be forced deterministically, so the exception is:
    # the route must map it to the same 409 role_name_taken as
    # RoleNameTakenError instead of a 500.
    from sqlalchemy.exc import IntegrityError

    admin = await _admin(client)
    role_id = (await client.post("/api/admin/roles", headers=admin, json={
        "scope": "global", "name": "auditor", "description": "",
        "permissions": []})).json()["id"]

    async def _lost_race(session, role, **kwargs):
        raise IntegrityError("UPDATE roles", {},
                             Exception("duplicate key uq_roles_scope_name"))

    monkeypatch.setattr("graphrag_ui.api.roles_routes.update_role", _lost_race)
    r = await client.patch(f"/api/admin/roles/{role_id}", headers=admin,
                           json={"name": "renamed", "description": "",
                                 "permissions": []})
    assert r.status_code == 409
    assert r.json()["code"] == "role_name_taken"
