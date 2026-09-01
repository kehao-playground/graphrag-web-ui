"""Settings editor API tests: hash optimistic lock, 409 conflict payload,
version history (task brief 3; frontend diff flow depends on the exact
conflict keys {"detail","code","current_content","current_hash"}).
"""

import hashlib
import uuid

from sqlalchemy import select

from graphrag_ui.adapters.models import SettingsVersion
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER
from graphrag_ui.services.projects import ws_path
from tests.test_projects import _activate, _setup_two_users


async def _alice(client, app):
    """Create the two default users, use the fake initializer, return alice's headers."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    return await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")


async def _make_project(client, headers, name="Settings"):
    r = await client.post(
        "/api/projects", headers=headers, json={"name": name, "input_file_type": "text"}
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _settings_path(pid: uuid.UUID):
    return ws_path(pid) / "settings.yaml"


async def _versions(db_session, pid):
    rows = (
        (
            await db_session.execute(
                select(SettingsVersion)
                .where(SettingsVersion.project_id == pid)
                .order_by(SettingsVersion.id)
            )
        )
        .scalars()
        .all()
    )
    return rows


async def test_get_returns_content_and_hash(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)

    r = await client.get(f"/api/projects/{pid}/settings", headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"content", "content_hash"}
    assert "input" in body["content"]  # settings.yaml written at project init
    assert len(body["content_hash"]) == 64
    int(body["content_hash"], 16)  # 64-hex
    assert body["content_hash"] == hashlib.sha256(_settings_path(pid).read_bytes()).hexdigest()


async def test_put_with_correct_hash_writes_disk_and_version(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    me = (await client.get("/api/auth/me", headers=alice)).json()["id"]

    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()
    new_content = "input:\n  type: text\n  file_pattern: '.*\\.md$$'\n"
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": new_content, "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 200, r.text
    new_hash = r.json()["content_hash"]
    assert new_hash != got["content_hash"]
    assert new_hash == hashlib.sha256(new_content.encode()).hexdigest()

    # disk content changed to exactly what was PUT
    assert _settings_path(pid).read_text() == new_content

    # one version row for this write, saved_by = actor
    rows = await _versions(db_session, pid)
    assert len(rows) == 1
    assert rows[0].content == new_content
    assert rows[0].content_hash == new_hash
    assert str(rows[0].saved_by) == me


async def test_put_with_stale_hash_returns_conflict_payload(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()

    # simulate a concurrent change directly on disk between GET and PUT
    disk_now = "input:\n  type: text\n"
    _settings_path(pid).write_text(disk_now)

    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={
            "content": "input:\n  type: text\n  base_limit: 5\n",
            "expected_hash": got["content_hash"],
        },
    )
    assert r.status_code == 409
    body = r.json()
    # exact keys — the frontend diff flow (task 7) depends on them
    assert set(body) == {"detail", "code", "current_content", "current_hash"}
    assert body["detail"] == "conflict"
    assert body["code"] == "settings_conflict"
    assert body["current_content"] == disk_now
    assert body["current_hash"] == hashlib.sha256(disk_now.encode()).hexdigest()

    # no write happened: disk unchanged, no version row
    assert _settings_path(pid).read_text() == disk_now
    assert await _versions(db_session, pid) == []


async def test_restore_flow_via_versions(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    v0 = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()

    # save v1, then v2 (the brief's restore flow: 3 writes → 3 version rows)
    v1_content = "input:\n  type: text\n  file_pattern: '.*\\.md$$'\n"
    h1 = (
        await client.put(
            f"/api/projects/{pid}/settings",
            headers=alice,
            json={"content": v1_content, "expected_hash": v0["content_hash"]},
        )
    ).json()["content_hash"]
    v2_content = "input:\n  type: text\n  file_pattern: '.*\\.csv$$'\n"
    h2 = (
        await client.put(
            f"/api/projects/{pid}/settings",
            headers=alice,
            json={"content": v2_content, "expected_hash": h1},
        )
    ).json()["content_hash"]

    # fetch v1 content from the history endpoint
    versions = (await client.get(f"/api/projects/{pid}/settings/versions", headers=alice)).json()
    assert len(versions) == 2
    v1_detail = (
        await client.get(
            f"/api/projects/{pid}/settings/versions/{versions[1]['id']}", headers=alice
        )
    ).json()
    assert v1_detail["content"] == v1_content

    # restore: PUT v1 content back with the fresh hash
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": v1_detail["content"], "expected_hash": h2},
    )
    assert r.status_code == 200

    assert _settings_path(pid).read_text() == v1_content
    rows = await _versions(db_session, pid)
    assert len(rows) == 3
    assert rows[-1].content_hash == h1  # restore re-snapshots v1


async def test_put_invalid_yaml_is_400_and_leaves_disk_intact(client, app, db_session):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()

    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": "not: [valid", "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 400

    assert _settings_path(pid).read_text() == got["content"]
    assert await _versions(db_session, pid) == []


async def test_put_oversized_content_is_400_and_leaves_disk_intact(client, app, db_session):
    """Content above the 1 MiB cap is rejected before any write — neither
    settings.yaml nor settings_versions may change."""
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()

    big = "k: " + "v" * (1024 * 1024)  # valid YAML, 3 bytes past the cap
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": big, "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "settings content too large"

    after = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()
    assert after["content_hash"] == got["content_hash"]  # disk untouched
    assert await _versions(db_session, pid) == []  # no version row


async def test_viewer_can_read_but_not_write(client, app):
    admin = await _setup_two_users(client)
    app.dependency_overrides[get_initializer] = FakeInitializer
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "ViewerRO", "input_file_type": "text"}
        )
    ).json()["id"]
    # bob's user id via the admin user list; add him as viewer
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    assert (
        await client.put(
            f"/api/projects/{pid}/members/{bob_id}",
            headers=alice,
            json={"role_id": str(ROLE_ID_VIEWER)},
        )
    ).status_code == 200

    got = (await client.get(f"/api/projects/{pid}/settings", headers=bob)).json()
    assert "input" in got["content"]

    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=bob,
        json={"content": "x: 1\n", "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 403
    assert _settings_path(uuid.UUID(pid)).read_text() == got["content"]


async def test_versions_list_newest_first_and_detail_keys(client, app):
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()
    h1 = (
        await client.put(
            f"/api/projects/{pid}/settings",
            headers=alice,
            json={
                "content": "input:\n  type: text\n  base_limit: 5\n",
                "expected_hash": got["content_hash"],
            },
        )
    ).json()["content_hash"]
    h2 = (
        await client.put(
            f"/api/projects/{pid}/settings",
            headers=alice,
            json={"content": "input:\n  type: text\n  base_limit: 10\n", "expected_hash": h1},
        )
    ).json()["content_hash"]

    r = await client.get(f"/api/projects/{pid}/settings/versions", headers=alice)
    assert r.status_code == 200
    versions = r.json()
    assert len(versions) == 2
    for item in versions:
        assert set(item) == {"id", "content_hash", "saved_by", "created_at"}
        assert item["created_at"]
    # newest first: the v2 write (h2) must come before the v1 write (h1)
    assert [v["content_hash"] for v in versions] == [h2, h1]

    detail = (
        await client.get(
            f"/api/projects/{pid}/settings/versions/{versions[0]['id']}", headers=alice
        )
    ).json()
    assert set(detail) == {"id", "content", "content_hash", "saved_by", "created_at"}
    assert detail["content_hash"] == h2

    # unknown version id → 404
    assert (
        await client.get(f"/api/projects/{pid}/settings/versions/999999", headers=alice)
    ).status_code == 404


async def test_put_stray_dollar_placeholder_is_400(client, app, db_session):
    """A lone $ passes yaml.safe_load but breaks graphrag's strict Template
    substitution (runs BEFORE yaml parse in graphrag 3.1.0), so the write
    must be rejected before it reaches disk."""
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()

    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": 'x: "a$"\n', "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 400

    # a ${...} reference with no matching env key is just as unloadable
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": "x: ${MISSING_KEY}\n", "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 400

    assert _settings_path(pid).read_text() == got["content"]
    assert await _versions(db_session, pid) == []


async def test_put_with_env_backed_placeholder_succeeds(client, app, db_session):
    """${GRAPHRAG_API_KEY} resolves when the workspace .env provides it —
    substitution mirrors graphrag load_config.py: os.environ overlaid by the
    workspace .env."""
    alice = await _alice(client, app)
    pid = await _make_project(client, alice)
    got = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()
    (ws_path(pid) / ".env").write_text("GRAPHRAG_API_KEY=sk-123456789\n")

    content = "models:\n  api_key: ${GRAPHRAG_API_KEY}\n"
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": content, "expected_hash": got["content_hash"]},
    )
    assert r.status_code == 200, r.text
    assert _settings_path(pid).read_text() == content
    assert len(await _versions(db_session, pid)) == 1
