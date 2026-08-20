"""Dry-run validation endpoint tests (spec §6.1/§6.2): synchronous
`graphrag index --dry-run`, editor+ only, pass-through result contract,
no audit rows. The slow test forks the real graphrag CLI twice (~15 s).
"""

import uuid

import pytest
from sqlalchemy import select

from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.services.projects import _ws_path
from tests.test_projects import _activate, _setup_two_users


async def _fake_project(client, app):
    """Two default users + fake-initializer project owned by alice."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "DryRun", "input_file_type": "text"})).json()["id"]
    return admin, alice, pid


async def _viewer_headers(client, admin, alice, pid):
    """Bob as project viewer, activated and logged in."""
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    return await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")


@pytest.mark.parametrize("canned", [
    {"ok": True, "output": "Dry run complete, exiting..."},
    {"ok": False, "output": "simulated validation failure"},
])
async def test_dry_run_returns_adapter_result_verbatim(
        client, app, db_session, monkeypatch, canned):
    _, alice, pid = await _fake_project(client, app)
    seen = []

    async def fake_dry_run(root):
        seen.append(root)
        return canned

    monkeypatch.setattr("graphrag_ui.api.dry_run_routes.dry_run", fake_dry_run)
    before = (await db_session.execute(
        select(AuditLog.id).where(AuditLog.target_id == pid))).all()

    r = await client.post(f"/api/projects/{pid}/dry-run", headers=alice)

    assert r.status_code == 200
    assert r.json() == canned
    # adapter receives the project workspace path, never client input
    assert seen == [_ws_path(uuid.UUID(pid))]
    # dry-run is not queued and writes no audit rows (spec §6.1)
    after = (await db_session.execute(
        select(AuditLog.id).where(AuditLog.target_id == pid))).all()
    assert after == before


async def test_dry_run_viewer_is_forbidden(client, app, monkeypatch):
    admin, alice, pid = await _fake_project(client, app)
    bob = await _viewer_headers(client, admin, alice, pid)

    async def must_not_run(root):
        raise AssertionError("dry_run must not execute for a viewer")

    monkeypatch.setattr("graphrag_ui.api.dry_run_routes.dry_run", must_not_run)
    assert (await client.post(f"/api/projects/{pid}/dry-run",
                              headers=bob)).status_code == 403


@pytest.mark.slow
async def test_real_dry_run_valid_then_corrupted_workspace(client, app):
    _, alice, pid = await _fake_project(client, app)

    r = await client.post(f"/api/projects/{pid}/dry-run", headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "Dry run complete" in body["output"]

    # corrupted settings.yaml → graphrag config load fails → ok False (200)
    (_ws_path(uuid.UUID(pid)) / "settings.yaml").write_text("{{{")
    r2 = await client.post(f"/api/projects/{pid}/dry-run", headers=alice)
    assert r2.status_code == 200
    assert r2.json()["ok"] is False
