ADMIN_PW = "admin-new-pass-1"


async def _admin_token(client):
    """The bootstrap admin starts with must_change_password=True — a usable token requires finishing the change."""
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": "admin-pass-123", "new_password": ADMIN_PW})
    r2 = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": ADMIN_PW})
    return {"Authorization": f"Bearer {r2.json()['access_token']}"}


async def test_admin_crud_and_audit(client, db_session):
    hdr = await _admin_token(client)
    r = await client.post("/api/admin/users", headers=hdr, json={
        "email": "u1@test.local", "display_name": "User One", "password": "pass-12345"})
    assert r.status_code == 201
    uid = r.json()["id"]
    r2 = await client.patch(f"/api/admin/users/{uid}", headers=hdr,
                            json={"display_name": "User 1b"})
    assert r2.json()["display_name"] == "User 1b"
    r3 = await client.post(f"/api/admin/users/{uid}/reset-password",
                           headers=hdr, json={"new_password": "reset-12345"})
    assert r3.status_code == 204
    # Verify the audit table directly — never infer the audit write from
    # "login fails after deactivation"
    from sqlalchemy import select

    from graphrag_ui.adapters.models import AuditLog
    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"user.created", "user.updated", "user.password_reset"} <= actions
    r4 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"is_active": False})
    assert r4.json()["is_active"] is False
    r5 = await client.post("/api/auth/login", json={
        "email": "u1@test.local", "password": "reset-12345"})
    assert r5.status_code == 401


async def test_non_admin_forbidden(client):
    hdr = await _admin_token(client)
    await client.post("/api/admin/users", headers=hdr, json={
        "email": "u2@test.local", "display_name": "U2", "password": "pass-12345"})
    r = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "pass-12345"})
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # New accounts have must_change_password=True -> the password must be
    # changed before other endpoints work
    await client.post("/api/auth/change-password", headers=tok, json={
        "current_password": "pass-12345", "new_password": "u2-pass-6789"})
    r2 = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "u2-pass-6789"})
    user_tok = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r3 = await client.get("/api/admin/users", headers=user_tok)
    assert r3.status_code == 403


async def test_open_user_list_accessible_to_non_admin(client):
    """GET /api/users: the narrow list for picking users in member management —
    usable by non-admins, 401 when not logged in, contains only
    id/email/display_name/is_active, ordered by email."""
    hdr = await _admin_token(client)
    await client.post("/api/admin/users", headers=hdr, json={
        "email": "aa@test.local", "display_name": "AA", "password": "pass-12345"})
    r = await client.post("/api/admin/users", headers=hdr, json={
        "email": "bb@test.local", "display_name": "BB", "password": "pass-12345"})
    # bb must still be listed while disabled (the frontend filters); the
    # is_active flag exists exactly for this
    await client.patch(f"/api/admin/users/{r.json()['id']}", headers=hdr,
                       json={"is_active": False})

    # Not logged in -> 401
    assert (await client.get("/api/users")).status_code == 401

    # Non-admin (password change completed) -> 200
    login = await client.post("/api/auth/login", json={
        "email": "aa@test.local", "password": "pass-12345"})
    tok = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await client.post("/api/auth/change-password", headers=tok, json={
        "current_password": "pass-12345", "new_password": "aa-pass-6789"})
    relogin = await client.post("/api/auth/login", json={
        "email": "aa@test.local", "password": "aa-pass-6789"})
    user_tok = {"Authorization": f"Bearer {relogin.json()['access_token']}"}

    r2 = await client.get("/api/users", headers=user_tok)
    assert r2.status_code == 200
    users = r2.json()
    assert [u["email"] for u in users] == [
        "aa@test.local", "admin@test.local", "bb@test.local"]  # ORDER BY email
    assert {u["is_active"] for u in users} == {True, False}
    for u in users:  # narrow schema: admin fields must not leak
        assert set(u) == {"id", "email", "display_name", "is_active"}


    # must_change_password incomplete -> the guard still blocks (using the
    # active new user cc)
    await client.post("/api/admin/users", headers=hdr, json={
        "email": "cc@test.local", "display_name": "CC", "password": "pass-12345"})
    login2 = await client.post("/api/auth/login", json={
        "email": "cc@test.local", "password": "pass-12345"})
    cc_tok = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    assert (await client.get("/api/users", headers=cc_tok)).status_code == 403


async def test_cannot_deactivate_last_active_admin(client):
    """Second admin exists but is inactive → deactivating the original must 400."""
    hdr = await _admin_token(client)
    r = await client.post("/api/admin/users", headers=hdr, json={
        "email": "adm2@test.local", "display_name": "Admin Two", "password": "pass-12345"})
    uid2 = r.json()["id"]
    # second admin via PATCH, then deactivated — original is the only active admin left
    await client.patch(f"/api/admin/users/{uid2}", headers=hdr, json={"role": "admin"})
    await client.patch(f"/api/admin/users/{uid2}", headers=hdr, json={"is_active": False})
    users = (await client.get("/api/admin/users", headers=hdr)).json()
    original = next(u for u in users if u["email"] == "admin@test.local")
    r2 = await client.patch(f"/api/admin/users/{original['id']}", headers=hdr,
                            json={"is_active": False})
    assert r2.status_code == 400
    # the 400 here is raised by the self-modification guard; the dedicated
    # last-active-admin branch is unreachable via PATCH (acting admin always counts)
    # system must not be locked out: the original admin stays active
    users_after = (await client.get("/api/admin/users", headers=hdr)).json()
    original_after = next(u for u in users_after if u["email"] == "admin@test.local")
    assert original_after["is_active"] is True



async def test_get_user_unknown_id_raises_user_not_found(db_session):
    """Direct service: unknown id → UserNotFound (the route maps it to 404)."""
    import uuid

    import pytest

    from graphrag_ui.services.users import UserNotFound, get_user

    with pytest.raises(UserNotFound):
        await get_user(db_session, uuid.uuid4())


async def test_patch_user_guarded_rejects_self_role_change(db_session):
    """Direct service: admin targeting self with role/is_active → SelfRoleChangeError."""
    import pytest

    from graphrag_ui.services.users import SelfRoleChangeError, create_user, patch_user_guarded

    admin = await create_user(db_session, "self@test.local", "Self",
                              "pass-12345", actor_id=None)
    with pytest.raises(SelfRoleChangeError):
        await patch_user_guarded(db_session, admin, admin.id,
                                 display_name=None, role="user", is_active=None)


async def test_patch_user_guarded_rejects_last_active_admin_demotion(db_session):
    """Direct service: demoting the only active admin → LastActiveAdminError.

    This branch is unreachable via the API (the acting admin always counts as
    another active admin, and self-changes are rejected first) — pin it here.
    """
    import pytest

    from graphrag_ui.services.users import (
        LastActiveAdminError,
        create_user,
        patch_user_guarded,
        update_user,
    )

    actor = await create_user(db_session, "actor@test.local", "Actor",
                              "pass-12345", actor_id=None)
    target = await create_user(db_session, "target@test.local", "Target",
                               "pass-12345", actor_id=None)
    await update_user(db_session, target, role="admin", actor_id=actor.id)
    # actor is a plain user → target is the only active admin in the table
    with pytest.raises(LastActiveAdminError):
        await patch_user_guarded(db_session, actor, target.id,
                                 display_name=None, role="user", is_active=None)