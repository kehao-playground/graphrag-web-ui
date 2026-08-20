"""File upload/list/delete API tests (spec §6.3 files endpoints, §10 quota & path safety).

Projects are created through the real API — real `graphrag init`, like Task 1's
slow test — because the files endpoints must work against the real workspace
layout (per task brief: no FakeInitializer in this module).
"""

import pytest
from sqlalchemy import select

from graphrag_ui.adapters.models import AuditLog
from graphrag_ui.config import get_settings
from tests.test_projects import _activate, _setup_two_users


async def _alice(client):
    """Create the two default users and return alice's auth headers."""
    await _setup_two_users(client)
    return await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")


async def _make_project(client, headers, name="Files", input_file_type="text"):
    r = await client.post("/api/projects", headers=headers,
                          json={"name": name, "input_file_type": input_file_type})
    assert r.status_code == 201
    return r.json()["id"]


async def _upload(client, headers, pid, name, data):
    return await client.post(f"/api/projects/{pid}/files", headers=headers,
                             files={"file": (name, data)})


async def _list(client, headers, pid):
    return (await client.get(f"/api/projects/{pid}/files", headers=headers)).json()


async def test_upload_list_delete_lifecycle(client, db_session):
    alice = await _alice(client)
    pid = await _make_project(client, alice)

    r = await _upload(client, alice, pid, "notes.md", b"# hello")
    assert r.status_code == 201
    assert r.json() == {"name": "notes.md", "size": 7}

    body = await _list(client, alice, pid)
    assert [f["name"] for f in body["files"]] == ["notes.md"]
    entry = body["files"][0]
    assert entry["size"] == 7
    assert isinstance(entry["modified_at"], str) and entry["modified_at"]
    assert body["usage_bytes"] == 7
    assert body["quota_bytes"] == 5000 * 1024 * 1024  # default PROJECT_QUOTA_MB

    # usage_bytes accumulates across files
    assert (await _upload(client, alice, pid, "data.txt", b"0123456789")).status_code == 201
    assert (await _list(client, alice, pid))["usage_bytes"] == 17

    # delete → 204, gone from list, usage drops; unknown name → 404
    assert (await client.delete(f"/api/projects/{pid}/files/notes.md",
                                headers=alice)).status_code == 204
    body = await _list(client, alice, pid)
    assert [f["name"] for f in body["files"]] == ["data.txt"]
    assert body["usage_bytes"] == 10
    assert (await client.delete(f"/api/projects/{pid}/files/notes.md",
                                headers=alice)).status_code == 404

    # audit trail carries both actions with payload {name, size}
    rows = (await db_session.execute(
        select(AuditLog.action, AuditLog.payload)
        .where(AuditLog.target_id == pid)
        .order_by(AuditLog.id))).all()
    by_action = {}
    for action, payload in rows:
        by_action.setdefault(action, []).append(payload)
    assert {"name": "notes.md", "size": 7} in by_action["file.uploaded"]
    assert {"name": "notes.md", "size": 7} in by_action["file.deleted"]


async def test_upload_rejects_invalid_names(client):
    alice = await _alice(client)
    pid = await _make_project(client, alice)

    for name in [
        "script.py",          # extension outside the project's whitelist
        "../evil.txt",        # path traversal
        "..\\evil.txt",       # windows separator
        "sub/notes.txt",      # path separator
        ".hidden.md",         # leading dot
        "noext",              # no extension
        "a" * 253 + ".md",    # 256 chars > 255
    ]:
        r = await _upload(client, alice, pid, name, b"x")
        assert r.status_code == 400, name

    # httpx sends no filename header for "" → FastAPI cannot bind the part to
    # UploadFile and rejects it at the validation layer (422)
    assert (await _upload(client, alice, pid, "", b"x")).status_code == 422

    # boundary: exactly 255 chars is accepted, 256 is not
    assert (await _upload(client, alice, pid, "a" * 252 + ".md",
                          b"x")).status_code == 201
    assert (await _upload(client, alice, pid, "a" * 253 + ".md",
                          b"x")).status_code == 400


async def test_upload_too_large(client):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    # default UPLOAD_MAX_FILE_MB=50 → 51 MiB must fail
    r = await _upload(client, alice, pid, "big.md", b"x" * (51 * 1024 * 1024))
    assert r.status_code == 413
    assert (await _list(client, alice, pid))["files"] == []


async def test_quota_exceeded(client, monkeypatch):
    monkeypatch.setenv("PROJECT_QUOTA_MB", "1")
    get_settings.cache_clear()  # lru_cache: env change needs a fresh Settings
    try:
        alice = await _alice(client)
        pid = await _make_project(client, alice)
        chunk = b"x" * (700 * 1024)  # two chunks together exceed 1 MiB
        assert (await _upload(client, alice, pid, "a.md", chunk)).status_code == 201
        assert (await _upload(client, alice, pid, "b.md", chunk)).status_code == 413
        body = await _list(client, alice, pid)
        assert [f["name"] for f in body["files"]] == ["a.md"]
        assert body["quota_bytes"] == 1024 * 1024
    finally:
        # restore for later tests even if asserts fail mid-way
        get_settings.cache_clear()


async def test_viewer_is_read_only(client):
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = await _make_project(client, alice)
    assert (await _upload(client, alice, pid, "notes.md", b"data")).status_code == 201

    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")

    assert (await client.get(f"/api/projects/{pid}/files", headers=bob)).status_code == 200
    assert (await _upload(client, bob, pid, "x.md", b"x")).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}/files/notes.md",
                                headers=bob)).status_code == 403
    # viewer's rejected writes must not have touched the file
    assert [f["name"] for f in (await _list(client, alice, pid))["files"]] == ["notes.md"]


# --- service unit tests (no DB / workspace needed) ---


def test_safe_name_accepts_whitelisted():
    from graphrag_ui.services.files import _safe_name

    assert _safe_name("text", "notes.txt") == "notes.txt"
    assert _safe_name("text", "notes.md") == "notes.md"
    assert _safe_name("csv", "data.csv") == "data.csv"
    assert _safe_name("json", "dump.json") == "dump.json"


@pytest.mark.parametrize("ftype,name", [
    ("text", "script.py"),      # extension outside the whitelist
    ("csv", "data.txt"),        # text extension on a csv project
    ("json", "dump.csv"),       # csv extension on a json project
    ("text", "notes.MD"),       # whitelist match is case-sensitive
    ("text", ""),               # empty
    ("text", ".hidden.md"),     # leading dot
    ("text", "noext"),          # no extension
    ("text", "a" * 253 + ".md"),  # 256 chars > 255
    ("text", "../evil.txt"),    # traversal
    ("text", "..\\evil.txt"),   # windows separator
    ("text", "sub/notes.txt"),  # path separator
])
def test_safe_name_rejects(ftype, name):
    from graphrag_ui.services.files import FileServiceError, _safe_name

    with pytest.raises(FileServiceError):
        _safe_name(ftype, name)


def test_limit_helpers_read_settings(monkeypatch):
    from graphrag_ui.services.files import max_file_bytes, quota_bytes

    monkeypatch.setenv("UPLOAD_MAX_FILE_MB", "1")
    monkeypatch.setenv("PROJECT_QUOTA_MB", "2")
    get_settings.cache_clear()
    try:
        assert max_file_bytes() == 1024 * 1024
        assert quota_bytes() == 2 * 1024 * 1024
    finally:
        get_settings.cache_clear()
