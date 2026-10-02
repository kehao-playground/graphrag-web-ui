"""The listing and health hot path (fix wave F21).

Every navigation lists files: GET /files, the per-project health behind the
sidebar badges, and the batch health behind the project list. These tests
pin what that path must NOT do any more — re-hash unchanged files (R1-69),
lock and commit when nothing is new (R1-70), or issue its queries once per
project (R1-71) — and that the start snapshot shares the listing's scan
(R1-96) and the batch shares the project list's visibility rule (R1-94).
"""

import asyncio
import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from helpers import FakeInitializer
from sqlalchemy import event, select

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import Job, Project, ProjectFile, ProjectMember, User
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Atom
from graphrag_ui.domain.role_catalog import ROLE_ID_VIEWER
from graphrag_ui.services import file_listing, file_tags, index_snapshots, input_scan
from graphrag_ui.services import health as health_service
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.projects import list_projects, ws_path

_OLD = time.time() - 3600  # an mtime well outside the racy window


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def hash_calls(monkeypatch) -> list[str]:
    """Counts sha256_file calls by file name (the real hash still runs)."""
    calls: list[str] = []
    real = input_scan.sha256_file

    def counting(path):
        calls.append(path.name)
        return real(path)

    monkeypatch.setattr(input_scan, "sha256_file", counting)
    # add_tags imports it by name; any other module doing so would hash
    # uncounted.
    monkeypatch.setattr(file_tags, "sha256_file", counting)
    monkeypatch.setattr(index_snapshots, "sha256_file", counting, raising=False)
    return calls


async def _owner(db_session) -> User:
    user = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
    db_session.add(user)
    await db_session.flush()
    return user


async def _project(db_session, owner: User, files: dict[str, str]) -> Project:
    project = Project(
        name="hot",
        slug=f"hot-{uuid.uuid4().hex[:8]}",
        owner_id=owner.id,
        input_file_type="text",
    )
    db_session.add(project)
    await db_session.flush()
    await FakeInitializer().init(ws_path(project.id), "text")
    await db_session.commit()
    for name, text in files.items():
        _write(project, name, text)
    return project


def _write(project: Project, name: str, text: str, mtime: float = _OLD) -> None:
    path = ws_path(project.id) / "input" / name
    path.write_text(text)
    os.utime(path, (mtime, mtime))


@contextmanager
def _statements(db_session):
    """Every SQL statement the session's engine sends while the block runs."""
    seen: list[str] = []
    engine = db_session.bind.sync_engine

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


# --- R1-69: the project_files row is the hash cache ---


async def test_a_second_listing_hashes_nothing_unchanged(db_session, workspaces, hash_calls):
    project = await _project(db_session, await _owner(db_session), {"a.md": "A", "b.md": "B"})
    first = await file_listing.list_files(db_session, project)
    assert sorted(hash_calls) == ["a.md", "b.md"]

    hash_calls.clear()
    second = await file_listing.list_files(db_session, project)
    assert hash_calls == []
    assert second["files"] == first["files"]


async def test_a_changed_file_is_rehashed_and_the_row_refreshed(db_session, workspaces, hash_calls):
    project = await _project(db_session, await _owner(db_session), {"a.md": "A", "b.md": "B"})
    await file_listing.list_files(db_session, project)

    _write(project, "a.md", "CHANGED", mtime=_OLD + 60)
    hash_calls.clear()
    listing = await file_listing.list_files(db_session, project)
    assert hash_calls == ["a.md"]

    sha = {f["name"]: f["sha256"] for f in listing["files"]}["a.md"]
    row = (
        await db_session.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id, ProjectFile.name == "a.md"
            )
        )
    ).scalar_one()
    await db_session.refresh(row)
    assert (row.sha256, row.size) == (sha, len("CHANGED"))


async def test_a_racily_recent_file_is_not_trusted_from_the_cache(
    db_session, workspaces, hash_calls
):
    """Filesystem timestamps are coarse: a same-size rewrite within one
    tick keeps size and mtime. A file modified inside the racy window is
    hashed again on every listing until its mtime has settled."""
    project = await _project(db_session, await _owner(db_session), {})
    _write(project, "a.md", "A", mtime=time.time())
    await file_listing.list_files(db_session, project)

    _write(project, "a.md", "B", mtime=os.stat(ws_path(project.id) / "input" / "a.md").st_mtime)
    hash_calls.clear()
    listing = await file_listing.list_files(db_session, project)
    assert hash_calls == ["a.md"]
    assert listing["files"][0]["sha256"] == hashlib.sha256(b"B").hexdigest()


async def test_tagging_a_tracked_file_does_not_hash_it(db_session, workspaces, hash_calls):
    owner = await _owner(db_session)
    project = await _project(db_session, owner, {"a.md": "A"})
    await file_listing.list_files(db_session, project)

    hash_calls.clear()
    await file_tags.add_tags(db_session, project, "a.md", ["x"], actor_id=owner.id)
    assert hash_calls == []


# --- R1-70: discovery locks and commits only when it inserts ---


async def test_listing_tracked_files_does_not_wait_for_the_project_lock(
    db_session, workspaces, migrated_db
):
    project = await _project(db_session, await _owner(db_session), {"a.md": "A"})
    await file_listing.list_files(db_session, project)  # discovers a.md

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as holder, factory() as reader:
            await lock_project(holder, project.id)
            listing = await asyncio.wait_for(
                file_listing.list_files(reader, await reader.get(Project, project.id)), 5
            )
            await holder.rollback()
    finally:
        await engine.dispose()
    assert [f["name"] for f in listing["files"]] == ["a.md"]


async def test_an_untracked_file_is_still_discovered_under_the_lock(db_session, workspaces):
    project = await _project(db_session, await _owner(db_session), {"a.md": "A"})
    with _statements(db_session) as seen:
        await file_listing.list_files(db_session, project)
    assert any("FOR UPDATE" in s for s in seen)
    names = (
        await db_session.execute(
            select(ProjectFile.name).where(ProjectFile.project_id == project.id)
        )
    ).scalars()
    assert list(names) == ["a.md"]


# --- R1-71: health queries do not multiply per project ---


async def test_project_health_reads_the_baseline_once(db_session, workspaces):
    project = await _project(db_session, await _owner(db_session), {"a.md": "A"})
    await file_listing.list_files(db_session, project)
    with _statements(db_session) as seen:
        await health_service.project_health(db_session, project)
    # Every read of a snapshot row selects its title_recovery column.
    assert sum("index_snapshots.title_recovery" in s for s in seen) == 1


async def test_batch_health_issues_a_fixed_number_of_queries(db_session, workspaces):
    owner = await _owner(db_session)
    projects = [await _project(db_session, owner, {"a.md": "A"}) for _ in range(3)]
    for p in projects:
        db_session.add(
            Job(
                project_id=p.id,
                type="index",
                method="fast",
                argv=[],
                queued_by=owner.id,
                status="failed",
                finished_at=datetime.now(UTC),
            )
        )
    await db_session.commit()
    perms = frozenset({Atom.projects_view_any})
    ids = [p.id for p in projects]
    await health_service.batch_health(db_session, owner, perms, ids)  # discovery

    with _statements(db_session) as one:
        await health_service.batch_health(db_session, owner, perms, ids[:1])
    with _statements(db_session) as three:
        out = await health_service.batch_health(db_session, owner, perms, ids)
    assert len(three) == len(one)
    assert all(entry["last_attempt"]["status"] == "failed" for entry in out.values())
    assert all(entry["last_index"] is None for entry in out.values())


# --- R1-96: the start snapshot shares the listing's scan and cache ---


async def test_the_start_snapshot_reuses_the_listing_hashes(db_session, workspaces, hash_calls):
    owner = await _owner(db_session)
    project = await _project(db_session, owner, {"a.md": "A", ".tmp-x": "partial"})
    await file_listing.list_files(db_session, project)
    job = Job(
        project_id=project.id,
        type="index",
        method="fast",
        argv=[],
        queued_by=owner.id,
        status="running",
    )
    db_session.add(job)
    await db_session.commit()

    hash_calls.clear()
    snap = await index_snapshots.capture_start(db_session, project.id, job.id)
    assert hash_calls == []
    assert await index_snapshots.entries_of(db_session, snap) == {
        "a.md": hashlib.sha256(b"A").hexdigest()
    }


# --- R1-94: one visibility rule for the list and the batch ---


async def test_list_projects_filters_by_ids_under_the_visibility_rule(db_session, workspaces):
    owner = await _owner(db_session)
    member = await _owner(db_session)
    a, b, hidden = [await _project(db_session, owner, {}) for _ in range(3)]
    for p in (a, b):
        db_session.add(ProjectMember(project_id=p.id, user_id=member.id, role_id=ROLE_ID_VIEWER))
    await db_session.commit()

    seen = await list_projects(db_session, member, frozenset(), ids=[a.id, hidden.id])
    assert [p.id for p in seen] == [a.id]
    everything = await list_projects(
        db_session, member, frozenset({Atom.projects_view_any}), ids=[a.id, hidden.id]
    )
    assert {p.id for p in everything} == {a.id, hidden.id}
