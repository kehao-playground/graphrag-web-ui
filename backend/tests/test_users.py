from graphrag_ui.domain.role_catalog import (
    ROLE_ID_OPS,
    ROLE_ID_USER_ADMIN,
)
from graphrag_ui.services.roles import LastUserManagerError

ADMIN_PW = "admin-new-pass-1"


async def _admin_token(client):
    """The bootstrap admin starts with must_change_password=True — a usable token requires finishing the change."""
    r = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
    )
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": "admin-pass-123", "new_password": ADMIN_PW},
    )
    r2 = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": ADMIN_PW}
    )
    return {"Authorization": f"Bearer {r2.json()['access_token']}"}


async def test_admin_crud_and_audit(client, db_session):
    hdr = await _admin_token(client)
    r = await client.post(
        "/api/admin/users",
        headers=hdr,
        json={
            "email": "u1@test.local",
            "display_name": "User One",
            "password": "pass-12345",
            "roles": [str(ROLE_ID_OPS)],
        },
    )
    assert r.status_code == 201
    assert [ro["name"] for ro in r.json()["roles"]] == ["ops"]
    uid = r.json()["id"]
    r2 = await client.patch(
        f"/api/admin/users/{uid}", headers=hdr, json={"display_name": "User 1b"}
    )
    assert r2.json()["display_name"] == "User 1b"
    r3 = await client.post(
        f"/api/admin/users/{uid}/reset-password", headers=hdr, json={"new_password": "reset-12345"}
    )
    assert r3.status_code == 204
    # Verify the audit table directly — never infer the audit write from
    # "login fails after deactivation"
    from sqlalchemy import select

    from graphrag_ui.adapters.models import AuditLog

    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"user.created", "user.updated", "user.password_reset"} <= actions
    r4 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"is_active": False})
    assert r4.json()["is_active"] is False
    r5 = await client.post(
        "/api/auth/login", json={"email": "u1@test.local", "password": "reset-12345"}
    )
    assert r5.status_code == 401


async def test_non_admin_forbidden(client):
    hdr = await _admin_token(client)
    await client.post(
        "/api/admin/users",
        headers=hdr,
        json={"email": "u2@test.local", "display_name": "U2", "password": "pass-12345"},
    )
    r = await client.post(
        "/api/auth/login", json={"email": "u2@test.local", "password": "pass-12345"}
    )
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # New accounts have must_change_password=True -> the password must be
    # changed before other endpoints work
    await client.post(
        "/api/auth/change-password",
        headers=tok,
        json={"current_password": "pass-12345", "new_password": "u2-pass-6789"},
    )
    r2 = await client.post(
        "/api/auth/login", json={"email": "u2@test.local", "password": "u2-pass-6789"}
    )
    user_tok = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r3 = await client.get("/api/admin/users", headers=user_tok)
    assert r3.status_code == 403


async def test_open_user_list_accessible_to_non_admin(client):
    """GET /api/users: the narrow list for picking users in member management —
    usable by non-admins, 401 when not logged in, contains only
    id/email/display_name/is_active, ordered by email."""
    hdr = await _admin_token(client)
    await client.post(
        "/api/admin/users",
        headers=hdr,
        json={"email": "aa@test.local", "display_name": "AA", "password": "pass-12345"},
    )
    r = await client.post(
        "/api/admin/users",
        headers=hdr,
        json={"email": "bb@test.local", "display_name": "BB", "password": "pass-12345"},
    )
    # bb must still be listed while disabled (the frontend filters); the
    # is_active flag exists exactly for this
    await client.patch(f"/api/admin/users/{r.json()['id']}", headers=hdr, json={"is_active": False})

    # Not logged in -> 401
    assert (await client.get("/api/users")).status_code == 401

    # Non-admin (password change completed) -> 200
    login = await client.post(
        "/api/auth/login", json={"email": "aa@test.local", "password": "pass-12345"}
    )
    tok = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await client.post(
        "/api/auth/change-password",
        headers=tok,
        json={"current_password": "pass-12345", "new_password": "aa-pass-6789"},
    )
    relogin = await client.post(
        "/api/auth/login", json={"email": "aa@test.local", "password": "aa-pass-6789"}
    )
    user_tok = {"Authorization": f"Bearer {relogin.json()['access_token']}"}

    r2 = await client.get("/api/users", headers=user_tok)
    assert r2.status_code == 200
    users = r2.json()
    assert [u["email"] for u in users] == [
        "aa@test.local",
        "admin@test.local",
        "bb@test.local",
    ]  # ORDER BY email
    assert {u["is_active"] for u in users} == {True, False}
    for u in users:  # narrow schema: admin fields must not leak
        assert set(u) == {"id", "email", "display_name", "is_active"}

    # must_change_password incomplete -> the guard still blocks (using the
    # active new user cc)
    await client.post(
        "/api/admin/users",
        headers=hdr,
        json={"email": "cc@test.local", "display_name": "CC", "password": "pass-12345"},
    )
    login2 = await client.post(
        "/api/auth/login", json={"email": "cc@test.local", "password": "pass-12345"}
    )
    cc_tok = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    assert (await client.get("/api/users", headers=cc_tok)).status_code == 403


async def test_cannot_deactivate_last_active_admin(client):
    """The last active users:manage holder is protected. While the bootstrap
    admin is the sole manager, any roles/is_active PATCH on that row 400s
    (the self-change guard — the acting manager always counts as another
    holder, so LastUserManagerError is unreachable via the API and is pinned
    at service level in the unit tests below). With a second manager
    granted, that manager may both strip and deactivate the bootstrap."""
    hdr = await _admin_token(client)
    users = (await client.get("/api/admin/users", headers=hdr)).json()
    uid = next(u["id"] for u in users if u["email"] == "admin@test.local")
    r1 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"is_active": False})
    assert r1.status_code == 400
    assert r1.json()["code"] == "user_self_change_forbidden"
    r2 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"roles": []})
    assert r2.status_code == 400
    assert r2.json()["code"] == "user_self_change_forbidden"
    # system must not be locked out: the bootstrap admin stays active
    users_after = (await client.get("/api/admin/users", headers=hdr)).json()
    assert next(u for u in users_after if u["email"] == "admin@test.local")["is_active"] is True

    # grant a second manager; adm2 can now strip AND deactivate the bootstrap
    r = await client.post(
        "/api/admin/users",
        headers=hdr,
        json={
            "email": "adm2@test.local",
            "display_name": "Admin Two",
            "password": "pass-12345",
            "roles": [str(ROLE_ID_USER_ADMIN), str(ROLE_ID_OPS)],
        },
    )
    assert r.status_code == 201
    first_login = await client.post(
        "/api/auth/login", json={"email": "adm2@test.local", "password": "pass-12345"}
    )
    tok = {"Authorization": f"Bearer {first_login.json()['access_token']}"}
    await client.post(
        "/api/auth/change-password",
        headers=tok,
        json={"current_password": "pass-12345", "new_password": "adm2-pass-6789"},
    )
    login = await client.post(
        "/api/auth/login", json={"email": "adm2@test.local", "password": "adm2-pass-6789"}
    )
    adm2 = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (
        await client.patch(f"/api/admin/users/{uid}", headers=adm2, json={"is_active": False})
    ).status_code == 200
    assert (
        await client.patch(f"/api/admin/users/{uid}", headers=adm2, json={"roles": []})
    ).status_code == 200


async def test_get_user_unknown_id_raises_user_not_found(db_session):
    """Direct service: unknown id → UserNotFound (the route maps it to 404)."""
    import uuid

    import pytest

    from graphrag_ui.services.users import UserNotFound, get_user

    with pytest.raises(UserNotFound):
        await get_user(db_session, uuid.uuid4())


async def test_patch_user_guarded_rejects_self_role_change(db_session):
    """Direct service: users:manage holder targeting self with
    roles/is_active → SelfRoleChangeError."""
    import pytest

    from graphrag_ui.services.users import SelfRoleChangeError, create_user, patch_user_guarded

    admin = await create_user(
        db_session, "self@test.local", "Self", "pass-12345", role_ids=None, actor_id=None
    )
    with pytest.raises(SelfRoleChangeError):
        await patch_user_guarded(
            db_session, admin, frozenset(), admin.id, display_name=None, role_ids=[], is_active=None
        )


async def test_patch_user_guarded_rejects_last_manager_loss(db_session):
    """Direct service: stripping or deactivating the only active
    users:manage holder → LastUserManagerError (shared class, spec §6.2).

    Unreachable via the API (the acting manager always counts as another
    holder, and self-changes are rejected first) — pinned here.
    """
    import pytest

    from graphrag_ui.services.users import create_user, patch_user_guarded

    actor = await create_user(
        db_session, "actor@test.local", "Actor", "pass-12345", role_ids=None, actor_id=None
    )
    target = await create_user(
        db_session,
        "target@test.local",
        "Target",
        "pass-12345",
        role_ids=[ROLE_ID_USER_ADMIN],
        actor_id=None,
    )
    # actor holds no grants → target is the only active manager; both ways
    # of losing the atom (strip via roles, deactivate) are guarded
    with pytest.raises(LastUserManagerError):
        await patch_user_guarded(
            db_session,
            actor,
            frozenset(),
            target.id,
            display_name=None,
            role_ids=[],
            is_active=None,
        )
    with pytest.raises(LastUserManagerError):
        await patch_user_guarded(
            db_session,
            actor,
            frozenset(),
            target.id,
            display_name=None,
            role_ids=None,
            is_active=False,
        )
    # a second manager existing makes both succeed
    await create_user(
        db_session,
        "second@test.local",
        "Second",
        "pass-12345",
        role_ids=[ROLE_ID_USER_ADMIN],
        actor_id=None,
    )
    stripped = await patch_user_guarded(
        db_session, actor, frozenset(), target.id, display_name=None, role_ids=[], is_active=None
    )
    assert stripped.is_active is True


async def test_patch_user_duplicate_role_ids_collapse_to_one_grant(client, db_session):
    """Duplicate role ids in one PATCH: 200 and exactly one grant — the
    double insert used to hit the UserRole PK (IntegrityError → 500)."""
    import uuid

    from sqlalchemy import func, select

    from graphrag_ui.adapters.models import UserRole

    hdr = await _admin_token(client)
    uid = (
        await client.post(
            "/api/admin/users",
            headers=hdr,
            json={"email": "dup@test.local", "display_name": "Dup", "password": "pass-12345"},
        )
    ).json()["id"]
    r = await client.patch(
        f"/api/admin/users/{uid}", headers=hdr, json={"roles": [str(ROLE_ID_OPS), str(ROLE_ID_OPS)]}
    )
    assert r.status_code == 200
    assert [ro["name"] for ro in r.json()["roles"]] == ["ops"]
    grants = (
        await db_session.execute(select(func.count()).where(UserRole.user_id == uuid.UUID(uid)))
    ).scalar_one()
    assert grants == 1
