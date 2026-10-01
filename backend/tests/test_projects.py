import pytest
import yaml

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.domain.role_catalog import ROLE_ID_OWNER, ROLE_ID_VIEWER


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """Every new account (incl. the bootstrap admin) has must_change_password=True — usable only after the change."""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _setup_two_users(client):
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    await client.post(
        "/api/admin/users",
        headers=admin,
        json={"email": "alice@test.local", "display_name": "Alice", "password": "alice-pass-1"},
    )
    await client.post(
        "/api/admin/users",
        headers=admin,
        json={"email": "bob@test.local", "display_name": "Bob", "password": "bob-pass-1234"},
    )
    return admin


@pytest.mark.slow
async def test_create_project_runs_init_and_adds_owner(client, tmp_path):
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post(
        "/api/projects", headers=alice, json={"name": "Research Corpus", "input_file_type": "text"}
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    ws = tmp_path / "ws" / pid
    assert (ws / "settings.yaml").exists()  # graphrag init really ran
    assert (ws / "input").exists()
    cfg = yaml.safe_load((ws / "settings.yaml").read_text())
    assert cfg["input"]["type"] == "text"  # never assert `"text" in yaml_text`:
    #   settings.yaml already contains strings like text-embedding-3-large, so
    #   that weaker form would pass even without the patch
    members = (await client.get(f"/api/projects/{pid}/members", headers=alice)).json()
    assert members[0]["email"] == "alice@test.local"
    assert members[0]["role_name"] == "owner"


async def test_permission_matrix_enforced(client, app):
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "P1", "input_file_type": "text"}
        )
    ).json()["id"]
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 403
    # alice adds bob as viewer -> he can read but not modify
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(
        f"/api/projects/{pid}/members/{bob_id}",
        headers=alice,
        json={"role_id": str(ROLE_ID_VIEWER)},
    )
    assert (await client.get(f"/api/projects/{pid}", headers=bob)).status_code == 200
    assert (
        await client.patch(f"/api/projects/{pid}", headers=bob, json={"name": "X"})
    ).status_code == 403
    # bob cannot manage members
    assert (
        await client.delete(f"/api/projects/{pid}/members/{bob_id}", headers=bob)
    ).status_code == 403
    # Non-owners cannot delete the project; the owner can
    assert (await client.delete(f"/api/projects/{pid}", headers=bob)).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}", headers=alice)).status_code == 204


async def test_delete_project_removes_workspace(client, app, tmp_path):
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "P2", "input_file_type": "csv"}
        )
    ).json()["id"]
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
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "Cascade", "input_file_type": "text"}
        )
    ).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(
        f"/api/projects/{pid}/members/{bob_id}",
        headers=alice,
        json={"role_id": str(ROLE_ID_VIEWER)},
    )
    assert (
        (await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == pid)))
        .scalars()
        .all()
    ), "precondition: members exist"
    db_session.expire_all()  # detach cache so cascade is observed fresh
    await client.delete(f"/api/projects/{pid}", headers=alice)
    rows = (
        (await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == pid)))
        .scalars()
        .all()
    )
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
    r = await client.post(
        "/api/projects", headers=alice, json={"name": "Exploder", "input_file_type": "text"}
    )
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "graphrag init failed"
    assert body["code"] == "init_failed"
    names = [p["name"] for p in (await client.get("/api/projects", headers=alice)).json()]
    assert "Exploder" not in names  # rollback left no residual row


async def test_init_failure_removes_the_half_built_workspace(client, app):
    """R1-28: init mkdirs before graphrag runs; a failure must not leave an
    orphan directory nothing would ever sweep."""
    from graphrag_ui.adapters.workspace import WorkspaceInitError
    from graphrag_ui.services.projects import workspaces_root

    class HalfwayInitializer:
        async def init(self, root, input_file_type):
            (root / "input").mkdir(parents=True)
            (root / "settings.yaml").write_text("partial: true\n")
            raise WorkspaceInitError("simulated graphrag init failure")

    app.dependency_overrides[get_initializer] = lambda: HalfwayInitializer()
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    root = workspaces_root()
    before = set(root.iterdir()) if root.exists() else set()
    r = await client.post(
        "/api/projects", headers=alice, json={"name": "Halfway", "input_file_type": "text"}
    )
    assert r.status_code == 500
    after = set(root.iterdir()) if root.exists() else set()
    assert after == before


async def test_concurrent_creates_with_one_name_both_succeed(client, app):
    """R1-91: both slug checks pass before either commits; the second
    insert then hits the unique index. It retries with a suffixed slug
    instead of answering a bare 500."""
    import asyncio

    class SlowInitializer(FakeInitializer):
        async def init(self, root, input_file_type):
            await asyncio.sleep(0.5)
            await super().init(root, input_file_type)

    app.dependency_overrides[get_initializer] = lambda: SlowInitializer()
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    body = {"name": "Twin", "input_file_type": "text"}
    a, b = await asyncio.gather(
        client.post("/api/projects", headers=alice, json=body),
        client.post("/api/projects", headers=alice, json=body),
    )
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    slugs = {a.json()["slug"], b.json()["slug"]}
    assert len(slugs) == 2 and "twin" in slugs
    assert all(r.json()["my_permissions"] for r in (a, b))


async def test_owner_role_not_grantable(client, app):
    # Single-owner policy: owner is fixed to the creator and not grantable via API.
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (
        await client.post(
            "/api/projects", headers=alice, json={"name": "Solo", "input_file_type": "text"}
        )
    ).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    r = await client.put(
        f"/api/projects/{pid}/members/{bob_id}", headers=alice, json={"role_id": str(ROLE_ID_OWNER)}
    )
    assert r.status_code == 400  # owner is fixed to the creator (single-owner policy)
    assert r.json()["code"] == "member_owner_protected"


async def test_create_and_patch_answer_the_callers_permissions(client, app):
    """R3-22: POST (and PATCH) returned my_permissions: [] while GET
    returned the owner's atoms — the contract lied for one round trip."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    created = await client.post(
        "/api/projects", headers=alice, json={"name": "Perms", "input_file_type": "text"}
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    fetched = (await client.get(f"/api/projects/{pid}", headers=alice)).json()
    assert fetched["my_permissions"]
    assert created.json()["my_permissions"] == fetched["my_permissions"]
    patched = await client.patch(f"/api/projects/{pid}", headers=alice, json={"name": "Perms 2"})
    assert patched.json()["my_permissions"] == fetched["my_permissions"]


async def test_every_project_response_names_its_owner(client, app):
    """R1-117: the list page renders owners from the project itself, not
    from a full users fetch; every route that answers a project agrees."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    created = await client.post(
        "/api/projects", headers=alice, json={"name": "Owned", "input_file_type": "text"}
    )
    pid = created.json()["id"]
    owner = {"owner_email": "alice@test.local", "owner_display_name": "Alice"}
    fetched = (await client.get(f"/api/projects/{pid}", headers=alice)).json()
    patched = (
        await client.patch(f"/api/projects/{pid}", headers=alice, json={"name": "O2"})
    ).json()
    # admin sees every project (view_any) without being a member
    listed = (await client.get("/api/projects", headers=admin)).json()
    for body in (created.json(), fetched, patched, *listed):
        assert {k: body[k] for k in owner} == owner
