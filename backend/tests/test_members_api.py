"""Project member routes end to end (R2-34): add, change role, same role,
remove, and each refusal, with the audit row every change writes."""

import uuid

import pytest
from helpers import FakeInitializer
from sqlalchemy import select

from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import (
    ROLE_ID_MAINTAINER,
    ROLE_ID_OPS,
    ROLE_ID_OWNER,
    ROLE_ID_VIEWER,
)
from tests.test_projects import _activate, _setup_two_users


@pytest.fixture
async def project(client, app):
    """alice owns a project; bob exists but is not a member yet."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "M", "input_file_type": "text"}
        )
    ).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    ids = {u["email"]: u["id"] for u in users}
    return {
        "pid": pid,
        "alice": alice,
        "alice_id": ids["alice@test.local"],
        "bob_id": ids["bob@test.local"],
    }


async def _put(client, p, uid, role_id):
    return await client.put(
        f"/api/projects/{p['pid']}/members/{uid}",
        headers=p["alice"],
        json={"role_id": str(role_id)},
    )


async def _members(client, p):
    r = await client.get(f"/api/projects/{p['pid']}/members", headers=p["alice"])
    return {m["email"]: m["role_name"] for m in r.json()}


async def _actions(db_session, pid) -> list[str]:
    rows = await db_session.execute(
        select(AuditLog.action)
        .where(AuditLog.target_id == pid, AuditLog.action.like("member.%"))
        .order_by(AuditLog.id)
    )
    return list(rows.scalars())


async def test_add_change_same_and_remove(client, project, db_session):
    p = project
    r = await _put(client, p, p["bob_id"], ROLE_ID_VIEWER)
    assert r.status_code == 200
    assert r.json() == {
        "user_id": p["bob_id"],
        "email": "bob@test.local",
        "display_name": "Bob",
        "role_id": str(ROLE_ID_VIEWER),
        "role_name": "viewer",
    }
    assert (await _put(client, p, p["bob_id"], ROLE_ID_MAINTAINER)).json()["role_name"] == (
        "maintainer"
    )
    # the same role again is a no-op answer, not a second change
    assert (await _put(client, p, p["bob_id"], ROLE_ID_MAINTAINER)).status_code == 200
    assert (await _members(client, p))["bob@test.local"] == "maintainer"

    r = await client.delete(f"/api/projects/{p['pid']}/members/{p['bob_id']}", headers=p["alice"])
    assert r.status_code == 204
    assert "bob@test.local" not in await _members(client, p)
    assert await _actions(db_session, p["pid"]) == [
        "member.added",
        "member.role_changed",
        "member.removed",
    ]


async def test_removing_a_non_member_is_member_not_found(client, project):
    p = project
    r = await client.delete(f"/api/projects/{p['pid']}/members/{p['bob_id']}", headers=p["alice"])
    assert (r.status_code, r.json()["code"]) == (404, "member_not_found")


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_the_owner_cannot_be_changed_or_removed(client, project, method):
    p = project
    url = f"/api/projects/{p['pid']}/members/{p['alice_id']}"
    if method == "put":
        r = await client.put(url, headers=p["alice"], json={"role_id": str(ROLE_ID_VIEWER)})
    else:
        r = await client.delete(url, headers=p["alice"])
    assert (r.status_code, r.json()["code"]) == (400, "member_owner_protected")
    assert (await _members(client, p))["alice@test.local"] == "owner"


@pytest.mark.parametrize(
    ("role_id", "status", "code"),
    [
        (ROLE_ID_OWNER, 400, "member_owner_protected"),  # single-owner policy
        (ROLE_ID_OPS, 400, "role_scope_mismatch"),  # a global role
        (uuid.uuid4(), 404, "role_not_found"),
    ],
    ids=["owner role", "global role", "unknown role"],
)
async def test_refused_roles(client, project, db_session, role_id, status, code):
    p = project
    r = await _put(client, p, p["bob_id"], role_id)
    assert (r.status_code, r.json()["code"]) == (status, code)
    assert "bob@test.local" not in await _members(client, p)
    assert await _actions(db_session, p["pid"]) == []


async def test_an_unknown_user_is_user_not_found(client, project):
    r = await _put(client, project, uuid.uuid4(), ROLE_ID_VIEWER)
    assert (r.status_code, r.json()["code"]) == (404, "user_not_found")
