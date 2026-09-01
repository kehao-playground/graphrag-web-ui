"""Read side of the audit log (GET /api/admin/audit).

Rows were written for user.created, file.uploaded, env.key_set,
user.role_promoted and more, and nothing could ever read them back: an audit
trail nobody can see is not an audit trail. These tests pin the guard, the
ordering, the filters and the actor join.
"""

import uuid

ADMIN_PW = "admin-new-pass-1"


async def _admin(client):
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


async def _make_user(client, admin, email, name="Someone"):
    r = await client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "email": email,
            "display_name": name,
            "password": "some-pass-123",
            "role_ids": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_requires_users_manage(client):
    admin = await _admin(client)
    await _make_user(client, admin, "plain@test.local")
    login = await client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "some-pass-123"}
    )
    hdr = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": "some-pass-123", "new_password": "plain-pass-456"},
    )
    login2 = await client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "plain-pass-456"}
    )
    plain = {"Authorization": f"Bearer {login2.json()['access_token']}"}

    r = await client.get("/api/admin/audit", headers=plain)
    assert r.status_code == 403
    assert r.json()["code"] == "admin_only"


async def test_unauthenticated_is_401(client):
    assert (await client.get("/api/admin/audit")).status_code == 401


async def test_lists_newest_first_with_total(client):
    admin = await _admin(client)
    await _make_user(client, admin, "one@test.local", "One")
    await _make_user(client, admin, "two@test.local", "Two")

    r = await client.get("/api/admin/audit", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"rows", "total"}
    assert body["total"] >= 3  # bootstrap admin + the two created above
    emails = [row["payload"]["email"] for row in body["rows"] if row["action"] == "user.created"]
    assert emails[0] == "two@test.local"  # newest first


async def test_row_shape_and_actor_join(client):
    admin = await _admin(client)
    created = await _make_user(client, admin, "shape@test.local")

    r = await client.get("/api/admin/audit", headers=admin, params={"action": "user.created"})
    row = next(x for x in r.json()["rows"] if x["target_id"] == created["id"])
    assert set(row) == {
        "id",
        "actor_id",
        "actor_email",
        "action",
        "target_type",
        "target_id",
        "payload",
        "created_at",
    }
    assert row["target_type"] == "user"
    # The actor is resolved to an email: a bare uuid makes the log unreadable
    assert row["actor_email"] == "admin@test.local"


async def test_bootstrap_row_has_no_actor(client):
    admin = await _admin(client)
    r = await client.get("/api/admin/audit", headers=admin, params={"action": "user.created"})
    bootstrap = [x for x in r.json()["rows"] if x["payload"].get("origin") == "bootstrap"]
    assert bootstrap and bootstrap[0]["actor_id"] is None
    assert bootstrap[0]["actor_email"] is None


async def test_filter_by_action_and_target_type(client):
    admin = await _admin(client)
    await _make_user(client, admin, "filt@test.local")

    r = await client.get("/api/admin/audit", headers=admin, params={"action": "user.created"})
    assert r.json()["rows"] and all(x["action"] == "user.created" for x in r.json()["rows"])

    r = await client.get("/api/admin/audit", headers=admin, params={"target_type": "user"})
    assert r.json()["rows"] and all(x["target_type"] == "user" for x in r.json()["rows"])

    r = await client.get("/api/admin/audit", headers=admin, params={"action": "nope.nothing"})
    assert r.json() == {"rows": [], "total": 0}


async def test_filter_by_actor(client):
    admin = await _admin(client)
    await _make_user(client, admin, "byactor@test.local")
    me = (await client.get("/api/auth/me", headers=admin)).json()

    r = await client.get("/api/admin/audit", headers=admin, params={"actor_id": me["id"]})
    assert r.json()["rows"] and all(x["actor_id"] == me["id"] for x in r.json()["rows"])

    other = str(uuid.uuid4())
    r = await client.get("/api/admin/audit", headers=admin, params={"actor_id": other})
    assert r.json() == {"rows": [], "total": 0}


async def test_pagination_keeps_total_unpaginated(client):
    admin = await _admin(client)
    for i in range(3):
        await _make_user(client, admin, f"page{i}@test.local")

    full = (await client.get("/api/admin/audit", headers=admin)).json()
    page = (
        await client.get("/api/admin/audit", headers=admin, params={"limit": 2, "offset": 1})
    ).json()
    assert len(page["rows"]) == 2
    assert page["total"] == full["total"]  # total ignores limit/offset
    assert [r["id"] for r in page["rows"]] == [r["id"] for r in full["rows"][1:3]]


async def test_limit_is_bounded(client):
    admin = await _admin(client)
    assert (
        await client.get("/api/admin/audit", headers=admin, params={"limit": 5000})
    ).status_code == 422
