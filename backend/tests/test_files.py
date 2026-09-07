"""File upload/list/delete API tests (spec §6.3 files endpoints, §10 quota & path safety).

Projects are created through the real API — real `graphrag init`, like Task 1's
slow test — because the files endpoints must work against the real workspace
layout (per task brief: no FakeInitializer in this module).
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from graphrag_ui.adapters.models import (
    AuditLog,
    IndexSnapshot,
    IndexSnapshotEntry,
    Job,
    Project,
    ProjectFile,
    User,
)
from graphrag_ui.config import get_settings
from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER
from graphrag_ui.services import files as files_service
from graphrag_ui.services.projects import ws_path
from tests.test_projects import _activate, _setup_two_users


async def _alice(client):
    """Create the two default users and return alice's auth headers."""
    await _setup_two_users(client)
    return await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")


async def _make_project(client, headers, name="Files", input_file_type="text"):
    r = await client.post(
        "/api/projects", headers=headers, json={"name": name, "input_file_type": input_file_type}
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _upload(client, headers, pid, name, data):
    return await client.post(
        f"/api/projects/{pid}/files", headers=headers, files={"file": (name, data)}
    )


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
    assert (
        await client.delete(f"/api/projects/{pid}/files/notes.md", headers=alice)
    ).status_code == 204
    body = await _list(client, alice, pid)
    assert [f["name"] for f in body["files"]] == ["data.txt"]
    assert body["usage_bytes"] == 10
    assert (
        await client.delete(f"/api/projects/{pid}/files/notes.md", headers=alice)
    ).status_code == 404

    # audit trail carries both actions with payload {name, size}
    rows = (
        await db_session.execute(
            select(AuditLog.action, AuditLog.payload)
            .where(AuditLog.target_id == pid)
            .order_by(AuditLog.id)
        )
    ).all()
    by_action = {}
    for action, payload in rows:
        by_action.setdefault(action, []).append(payload)
    assert {"name": "notes.md", "size": 7} in by_action["file.uploaded"]
    assert {"name": "notes.md", "size": 7} in by_action["file.deleted"]


async def test_upload_rejects_invalid_names(client):
    alice = await _alice(client)
    pid = await _make_project(client, alice)

    for name in [
        "script.py",  # extension outside the project's whitelist
        "../evil.txt",  # path traversal
        "..\\evil.txt",  # windows separator
        "sub/notes.txt",  # path separator
        ".hidden.md",  # leading dot
        "noext",  # no extension
        "a" * 253 + ".md",  # 256 chars > 255
    ]:
        r = await _upload(client, alice, pid, name, b"x")
        assert r.status_code == 400, name

    # httpx sends no filename header for "" → FastAPI cannot bind the part to
    # UploadFile and rejects it at the validation layer (422)
    assert (await _upload(client, alice, pid, "", b"x")).status_code == 422

    # boundary: exactly 255 chars is accepted, 256 is not
    assert (await _upload(client, alice, pid, "a" * 252 + ".md", b"x")).status_code == 201
    assert (await _upload(client, alice, pid, "a" * 253 + ".md", b"x")).status_code == 400


async def test_upload_too_large(client):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    # default UPLOAD_MAX_FILE_MB=50 → 51 MiB must fail
    r = await _upload(client, alice, pid, "big.md", b"x" * (51 * 1024 * 1024))
    assert r.status_code == 413
    assert (await _list(client, alice, pid))["files"] == []


async def test_upload_too_large_without_content_length_streams_to_413(client):
    """Chunked upload (no Content-Length header): the streaming cap in
    save_file must abort at the limit instead of materializing the body
    (spec §8.2 — uploads share the pod's memory budget with the indexer)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)

    # hand-rolled multipart over an async generator: httpx cannot precompute
    # a length, so the request carries no Content-Length header at all
    async def multipart():
        yield (b'--B\r\nContent-Disposition: form-data; name="file"; filename="big.md"\r\n\r\n')
        chunk = b"x" * (1024 * 1024)
        for _ in range(60):  # 60 MiB, comfortably past the 50 MiB cap
            yield chunk
        yield b"\r\n--B--\r\n"

    r = await client.post(
        f"/api/projects/{pid}/files",
        headers={**alice, "Content-Type": "multipart/form-data; boundary=B"},
        content=multipart(),
    )
    assert r.status_code == 413
    assert (await _list(client, alice, pid))["files"] == []
    input_dir = ws_path(uuid.UUID(pid)) / "input"
    residue = list(input_dir.iterdir()) if input_dir.exists() else []
    assert residue == []  # no partial file, no dot-tmp residue


async def test_upload_rejects_oversized_content_length_before_read(client, monkeypatch):
    """A present, over-cap Content-Length is refused before any read — a
    declared multi-GB body must never reach the parser or the workspace."""
    from graphrag_ui.services import files as files_service

    async def _fail(*args, **kwargs):
        raise AssertionError("save_file must not run when Content-Length is over the cap")

    monkeypatch.setattr(files_service, "save_file", _fail)
    alice = await _alice(client)
    pid = await _make_project(client, alice)

    # 51 MiB clears the 50 MiB cap plus the multipart-framing slack; httpx
    # sets the real Content-Length for a bytes body
    r = await _upload(client, alice, pid, "big.md", b"x" * (51 * 1024 * 1024))
    assert r.status_code == 413
    assert r.json()["detail"] == "file exceeds the 50 MiB upload limit"
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
    await client.put(
        f"/api/projects/{pid}/members/{bob_id}",
        headers=alice,
        json={"role_id": str(ROLE_ID_VIEWER)},
    )
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")

    assert (await client.get(f"/api/projects/{pid}/files", headers=bob)).status_code == 200
    assert (await _upload(client, bob, pid, "x.md", b"x")).status_code == 403
    assert (
        await client.delete(f"/api/projects/{pid}/files/notes.md", headers=bob)
    ).status_code == 403
    # viewer's rejected writes must not have touched the file
    assert [f["name"] for f in (await _list(client, alice, pid))["files"]] == ["notes.md"]


# --- service unit tests (no DB / workspace needed) ---


def test_safe_name_accepts_whitelisted():
    from graphrag_ui.services.files import _safe_name

    assert _safe_name("text", "notes.txt") == "notes.txt"
    assert _safe_name("text", "notes.md") == "notes.md"
    assert _safe_name("csv", "data.csv") == "data.csv"
    assert _safe_name("json", "dump.json") == "dump.json"
    assert _safe_name("text", "NOTES.MD") == "NOTES.MD"  # Windows-style uppercase
    assert _safe_name("text", "report.Txt") == "report.Txt"


@pytest.mark.parametrize(
    "ftype,name",
    [
        ("text", "script.py"),  # extension outside the whitelist
        ("csv", "data.txt"),  # text extension on a csv project
        ("json", "dump.csv"),  # csv extension on a json project
        ("csv", "data.TXT"),  # uppercase ext of another type is still wrong
        ("text", ""),  # empty
        ("text", ".hidden.md"),  # leading dot
        ("text", "noext"),  # no extension
        ("text", "a" * 253 + ".md"),  # 256 chars > 255
        ("text", "../evil.txt"),  # traversal
        ("text", "..\\evil.txt"),  # windows separator
        ("text", "sub/notes.txt"),  # path separator
    ],
)
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


@pytest.fixture
def project(monkeypatch, tmp_path):
    """Unsaved Project model — usage_bytes only reads .id, so no DB needed.
    WORKSPACES_DIR points at an empty tmp dir: the scan touches no real data
    and stays hermetic even if this repo sits on a slow disk."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    try:
        yield Project(
            id=uuid.uuid4(), name="pin", slug="pin", owner_id=uuid.uuid4(), input_file_type="text"
        )
    finally:
        # restore for later tests even if asserts fail mid-way
        get_settings.cache_clear()


async def test_usage_bytes_is_awaitable(project):
    # Regression pin: usage_bytes must be a coroutine function — a sync
    # rglob on the event loop froze large-workspace requests (spec A4).
    import inspect

    assert inspect.iscoroutinefunction(files_service.usage_bytes)
    n = await files_service.usage_bytes(project)
    assert n >= 0


# --- transaction rollback tests (spec A1: services own audit + commit) ---


async def test_upload_rollback_leaves_no_audit_row_when_stream_fails(db_session, project):
    """A reader that fails mid-stream: save_file must roll back the audit
    row AND remove the tmp file; the workspace stays clean (spec A1)."""

    class Boom:
        async def read(self, n):
            raise RuntimeError("stream broke")

    with pytest.raises(RuntimeError, match="stream broke"):
        await files_service.save_file(db_session, project, "ok.txt", Boom(), actor_id=uuid.uuid4())
    input_dir = ws_path(project.id) / "input"
    assert not list(input_dir.glob(".tmp-*"))
    rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "file.uploaded"))
    ).scalars()
    assert list(rows) == []


async def test_upload_rollback_leaves_no_audit_row_when_rename_fails(
    db_session, project, monkeypatch
):
    """Post-audit external failure (the atomic rename): the flushed
    file.uploaded row must roll back and leave no tmp or target file —
    the audit row never outlives the work it describes (spec A1)."""
    # Task 3 wires uploads to project_files (FKs to projects/users, Task
    # 1's model), so the fixture's unsaved Project and a phantom actor
    # need real rows for the insert to reach the monkeypatched rename.
    owner = User(
        id=uuid.uuid4(), email="rename-owner@test.local", password_hash="x", display_name="o"
    )
    actor = User(
        id=uuid.uuid4(), email="rename-actor@test.local", password_hash="x", display_name="a"
    )
    db_session.add_all([owner, actor])
    await db_session.commit()
    project.owner_id = owner.id
    db_session.add(project)
    await db_session.commit()
    pid = project.id  # save_file's rollback expires session instances

    chunks = iter([b"hello"])

    class Reader:
        async def read(self, n):
            return next(chunks, b"")

    def _boom(src, dst):
        raise OSError("rename failed")

    monkeypatch.setattr(files_service.os, "replace", _boom)
    with pytest.raises(OSError, match="rename failed"):
        await files_service.save_file(db_session, project, "ok.txt", Reader(), actor_id=actor.id)
    input_dir = ws_path(pid) / "input"
    assert not list(input_dir.glob(".tmp-*"))  # finally-cleaned tmp
    assert not (input_dir / "ok.txt").exists()  # rename never landed
    rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "file.uploaded"))
    ).scalars()
    assert list(rows) == []


# --- listing: union enumeration, discovery, ingest check (spec 6.2/6.3) ---


async def _seed_baseline(
    db_session, pid: str, entries: dict[str, str], *, attributable: list[str], recovery: str
) -> None:
    """The state a succeeded index job leaves behind: a promoted baseline
    (kind='baseline') with its entries and projects.baseline_snapshot_id
    pointing at it. Seeded as rows instead of forking the real CLI — the
    API-created project and uploads already exercised the real paths."""
    project = (
        await db_session.execute(select(Project).where(Project.id == uuid.UUID(pid)))
    ).scalar_one()
    job = Job(
        project_id=project.id,
        type="index",
        method="fast",
        argv=["index", "--root", str(ws_path(project.id)), "--method", "fast"],
        queued_by=project.owner_id,
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()
    snap = IndexSnapshot(
        job_id=job.id,
        project_id=project.id,
        kind="baseline",
        attributable_titles=attributable,
        title_recovery=recovery,
        artifact_epoch=1,
    )
    db_session.add(snap)
    await db_session.flush()
    start = IndexSnapshot(
        job_id=job.id,
        project_id=project.id,
        kind="start",
        attributable_titles=attributable,
        title_recovery=recovery,
    )
    db_session.add(start)
    await db_session.flush()
    db_session.add_all(
        IndexSnapshotEntry(snapshot_id=snap.id, name=name, sha256=sha)
        for name, sha in entries.items()
    )
    db_session.add_all(
        IndexSnapshotEntry(snapshot_id=start.id, name=name, sha256=sha)
        for name, sha in entries.items()
    )
    project.baseline_snapshot_id = snap.id
    project.artifact_epoch = 1
    await db_session.commit()


def _stub_parquet(pid: str) -> None:
    """A documents.parquet the ingest check can stat; content is irrelevant
    — the check proves existence and nothing more."""
    out = ws_path(uuid.UUID(pid)) / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "documents.parquet").write_bytes(b"stub-parquet")


@pytest.fixture
async def indexed_project(client, db_session):
    """a.md + b.md uploaded through the API and indexed: a baseline with
    available recovery, both names attributable, output/ holding a
    documents.parquet. Yields (alice headers, pid)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    await _upload(client, alice, pid, "a.md", b"A")
    await _upload(client, alice, pid, "b.md", b"B")
    _stub_parquet(pid)
    await _seed_baseline(
        db_session,
        pid,
        {"a.md": hashlib.sha256(b"A").hexdigest(), "b.md": hashlib.sha256(b"B").hexdigest()},
        attributable=["a.md", "b.md"],
        recovery="available",
    )
    return alice, pid


@pytest.fixture
async def title_column_project(client, db_session):
    """Indexed while input.title_column was set, then the setting removed
    without rebuilding: today's settings.yaml has no title_column, the
    baseline row still records the unavailable recovery. Yields (alice
    headers, pid)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    await _upload(client, alice, pid, "a.md", b"A")
    _stub_parquet(pid)
    await _seed_baseline(
        db_session,
        pid,
        {"a.md": hashlib.sha256(b"A").hexdigest()},
        attributable=[],
        recovery="unavailable_title_column",
    )
    return alice, pid


@pytest.fixture
async def skipped_project(client, db_session):
    """The rule ran and matched nothing: available recovery with an EMPTY
    attributable set — a different fact from unavailable, and the only one
    that produces `skipped`. Yields (alice headers, pid)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    await _upload(client, alice, pid, "a.md", b"A")
    _stub_parquet(pid)
    await _seed_baseline(
        db_session,
        pid,
        {"a.md": hashlib.sha256(b"A").hexdigest()},
        attributable=[],
        recovery="available",
    )
    return alice, pid


async def test_listing_reports_new_before_any_index(client):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    await _upload(client, alice, pid, "a.md", b"A")

    body = await _list(client, alice, pid)
    assert body["ingest_check"] == "unavailable_no_baseline"
    assert body["has_baseline"] is False
    assert [(f["name"], f["index_state"]) for f in body["files"]] == [("a.md", "new")]
    assert body["files"][0]["sha256"]


async def test_deleted_file_still_lists_as_removed_with_null_columns(
    client, db_session, indexed_project
):
    """A removed row has no file behind it: size, modified_at and sha256 are
    NULL rather than an invented zero, and files.total still counts it."""
    alice, pid = indexed_project  # baseline: a.md, b.md
    assert (
        await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)
    ).status_code == 204

    body = await _list(client, alice, pid)
    row = next(f for f in body["files"] if f["name"] == "a.md")
    assert row["index_state"] == "removed"
    assert row["size"] is None and row["modified_at"] is None and row["sha256"] is None
    assert row["tags"] == []
    assert len(body["files"]) == 2


async def test_no_file_operation_applies_to_a_removed_row(client, indexed_project):
    alice, pid = indexed_project
    await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)

    assert (
        await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)
    ).status_code == 404
    r = await client.get(f"/api/projects/{pid}/files/a.md/preview", headers=alice)
    assert r.status_code == 404


async def test_modified_and_indexed_are_distinguished_by_hash(client, indexed_project):
    alice, pid = indexed_project
    await _upload(client, alice, pid, "a.md", b"CHANGED")

    body = await _list(client, alice, pid)
    states = {f["name"]: f["index_state"] for f in body["files"]}
    assert states == {"a.md": "modified", "b.md": "indexed"}


async def test_untracked_file_is_discovered_idempotently(client, db_session, tmp_path):
    """Files that predate this release have no project_files row and nothing
    on disk records who uploaded them, so provenance is NULL and the UI
    renders the uploader as a dash rather than attributing the file to
    whoever opened the page."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    (ws_path(uuid.UUID(pid)) / "input" / "legacy.md").write_bytes(b"legacy")

    first = await _list(client, alice, pid)
    second = await _list(client, alice, pid)
    assert first["files"] == second["files"]

    rows = (
        (
            await db_session.execute(
                select(ProjectFile).where(ProjectFile.project_id == uuid.UUID(pid))
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].uploaded_by is None and rows[0].uploaded_at is None
    assert rows[0].discovered_at is not None
    assert rows[0].sha256 == hashlib.sha256(b"legacy").hexdigest()


async def test_ingest_check_reports_missing_output_under_an_existing_baseline(
    client, indexed_project
):
    """A cheap stat, not a read: it proves existence and nothing more. A
    present-but-corrupt parquet is outside what this detects, and the
    guarantee is worded as missing output, not healthy output."""
    alice, pid = indexed_project
    (ws_path(uuid.UUID(pid)) / "output" / "documents.parquet").unlink()

    body = await _list(client, alice, pid)
    assert body["ingest_check"] == "unavailable_not_indexed"
    assert body["has_baseline"] is True
    assert all(f["index_state"] != "skipped" for f in body["files"])


async def test_title_column_provenance_survives_a_later_settings_edit(
    client, db_session, title_column_project
):
    """Index under title_column, then remove the setting without rebuilding:
    listing must still report unavailable_title_column and emit no skipped,
    because it reads the baseline row rather than today's settings.yaml. The
    earlier design produced a screen of false alarms from a config edit."""
    alice, pid = title_column_project
    body = await _list(client, alice, pid)
    assert body["ingest_check"] == "unavailable_title_column"
    assert all(f["index_state"] != "skipped" for f in body["files"])


async def test_empty_attributable_set_with_available_recovery_yields_skipped(
    client, skipped_project
):
    """`attributable_titles = []` with title_recovery = 'available' is a
    different fact from unavailable, and only this one produces skipped."""
    alice, pid = skipped_project
    body = await _list(client, alice, pid)
    assert body["ingest_check"] == "available"
    assert {f["index_state"] for f in body["files"]} == {"skipped"}
