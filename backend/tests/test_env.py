"""Project .env API tests: masked reads and per-key upsert/delete (task brief 4).

Values are secrets: no response text — including error payloads — may ever
contain a plaintext value. FakeInitializer creates no .env, so the
missing-file path (empty list) is the pre-PATCH state in every test.
"""

import uuid

from sqlalchemy import select

from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.services.projects import _ws_path
from tests.test_projects import _activate, _setup_two_users

SECRET = "sk-123456789"


async def _alice(client, app):
    """Create the two default users, use the fake initializer, return alice's headers."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    return await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")


async def _make_project(client, headers, name="Env"):
    r = await client.post("/api/projects", headers=headers,
                          json={"name": name, "input_file_type": "text"})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _env_path(pid: uuid.UUID):
    return _ws_path(pid) / ".env"


async def _set(client, headers, pid, key, value):
    return await client.patch(f"/api/projects/{pid}/env", headers=headers,
                              json={"key": key, "value": value})


async def _env_audit(db_session, pid):
    """env.* audit rows for this project, in order (project.created excluded)."""
    return (await db_session.execute(
        select(AuditLog.action, AuditLog.payload)
        .where(AuditLog.target_id == str(pid),
               AuditLog.action.in_(["env.key_set", "env.key_deleted"]))
        .order_by(AuditLog.id))).all()


async def test_patch_get_masked_cycle(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)

    # missing .env → empty list
    r = await client.get(f"/api/projects/{pid}/env", headers=alice)
    assert r.status_code == 200, r.text
    assert r.json() == {"keys": []}

    r = await _set(client, alice, pid, "GRAPHRAG_API_KEY", SECRET)
    assert r.status_code == 204, r.text
    assert r.text == ""

    # masked read: exact shape, plaintext nowhere in the response text
    r = await client.get(f"/api/projects/{pid}/env", headers=alice)

    assert r.status_code == 200
    assert r.json() == {"keys": [{"key": "GRAPHRAG_API_KEY", "masked": "sk****"}]}
    assert SECRET not in r.text

    # disk carries the real line
    assert "GRAPHRAG_API_KEY=sk-123456789" in _env_path(pid).read_text()

    # audit carries the key only — never the value
    assert await _env_audit(db_session, pid) == [
        ("env.key_set", {"key": "GRAPHRAG_API_KEY"})]


async def test_patch_same_key_replaces_in_place(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    assert (await _set(client, alice, pid, "GRAPHRAG_API_KEY", SECRET)).status_code == 204
    assert (await _set(client, alice, pid, "GRAPHRAG_API_KEY",
                       "new-secret-value-9")).status_code == 204

    lines = [ln for ln in _env_path(pid).read_text().splitlines() if ln]
    assert lines == ["GRAPHRAG_API_KEY=new-secret-value-9"]
    r = await client.get(f"/api/projects/{pid}/env", headers=alice)
    assert r.json()["keys"] == [{"key": "GRAPHRAG_API_KEY", "masked": "ne****"}]


async def test_patch_preserves_other_lines_and_order(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    _env_path(pid).write_text(
        "# graphrag init placeholder\nGRAPHRAG_API_KEY=<API_KEY>\nOTHER_KEY=keepme\n")

    assert (await _set(client, alice, pid, "GRAPHRAG_API_KEY", SECRET)).status_code == 204

    assert _env_path(pid).read_text() == (
        "# graphrag init placeholder\nGRAPHRAG_API_KEY=sk-123456789\nOTHER_KEY=keepme\n")
    r = await client.get(f"/api/projects/{pid}/env", headers=alice)
    assert r.json()["keys"] == [
        {"key": "GRAPHRAG_API_KEY", "masked": "sk****"},
        {"key": "OTHER_KEY", "masked": "ke****"},
    ]


async def test_delete_removes_key_from_list_and_disk(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    assert (await _set(client, alice, pid, "GRAPHRAG_API_KEY", SECRET)).status_code == 204

    r = await client.delete(f"/api/projects/{pid}/env/GRAPHRAG_API_KEY", headers=alice)
    assert r.status_code == 204, r.text

    r = await client.get(f"/api/projects/{pid}/env", headers=alice)
    assert r.json() == {"keys": []}
    assert "GRAPHRAG_API_KEY" not in _env_path(pid).read_text()

    assert await _env_audit(db_session, pid) == [
        ("env.key_set", {"key": "GRAPHRAG_API_KEY"}),
        ("env.key_deleted", {"key": "GRAPHRAG_API_KEY"}),
    ]


async def test_delete_unknown_key_is_404(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)

    r = await client.delete(f"/api/projects/{pid}/env/NOPE", headers=alice)
    assert r.status_code == 404
    assert SECRET not in r.text
    assert await _env_audit(db_session, pid) == []


async def test_invalid_key_is_400_without_leaking_value(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)

    r = await _set(client, alice, pid, "bad-key", SECRET)
    assert r.status_code == 400, r.text
    assert SECRET not in r.text          # error payload never echoes the value
    assert not _env_path(pid).exists()   # rejected before touching disk


async def test_patch_oversized_value_is_400_and_leaves_disk_unchanged(client, app):
    """A value past the 64 KiB cap is rejected with a fixed message — the
    value must appear nowhere in the response, and never reach the disk."""
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    value = "v" * (64 * 1024 + 1)

    r = await _set(client, alice, pid, "GRAPHRAG_API_KEY", value)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "value too large"
    assert value not in r.text          # error payload never echoes the value
    assert not _env_path(pid).exists()  # rejected before touching disk


async def test_viewer_reads_but_cannot_write(client, app):
    admin = await _setup_two_users(client)
    app.dependency_overrides[get_initializer] = FakeInitializer
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = await _make_project(client, alice)
    assert (await _set(client, alice, pid, "GRAPHRAG_API_KEY", SECRET)).status_code == 204

    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    assert (await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                             json={"role": "viewer"})).status_code == 200
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")

    r = await client.get(f"/api/projects/{pid}/env", headers=bob)
    assert r.status_code == 200                          # viewer+ can read (masked)
    assert r.json()["keys"] == [{"key": "GRAPHRAG_API_KEY", "masked": "sk****"}]
    assert (await _set(client, bob, pid, "GRAPHRAG_API_KEY", "evil")).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}/env/GRAPHRAG_API_KEY",
                                headers=bob)).status_code == 403
    assert "GRAPHRAG_API_KEY=sk-123456789" in _env_path(pid).read_text()
