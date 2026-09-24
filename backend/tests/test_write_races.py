"""Write-path races and the input-mutation transaction (fix wave F5).

Each race test holds the project lock from a third session, starts the
competing writers, proves they wait on it, then releases: only an
interleaving tells a check made inside the lock from one made before it.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import AuditLog, Project, ProjectFile, Question
from graphrag_ui.config import get_settings
from graphrag_ui.services import env_file as env_service
from graphrag_ui.services import files as files_service
from graphrag_ui.services import questions as questions_service
from graphrag_ui.services import settings as settings_service
from graphrag_ui.services.files import QuotaExceededError
from graphrag_ui.services.project_lock import input_mutation, lock_project
from graphrag_ui.services.projects import ws_path
from tests.test_files import (
    _alice,
    _make_project,
    indexed_project,  # noqa: F401  (pytest fixture; test params shadow it)
)
from tests.test_project_freeze import _Bytes
from tests.test_questions import project_with_set  # noqa: F401  (pytest fixture)


async def _project_ids(client, db_session):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    return alice, pid, project.id, project.owner_id


async def _race(holder, *tasks: asyncio.Task) -> list:
    """Prove every writer waits on the lock `holder` took, then release it
    and collect the outcomes. The release sits in a finally: sessions close
    in reverse order, and closing one whose query waits on a lock the
    still-open holder owns would hang the test instead of failing it."""
    try:
        done, _ = await asyncio.wait(set(tasks), timeout=1.0)
        assert done == set(), "a writer did not wait for the project lock"
    finally:
        await holder.commit()
    return await asyncio.gather(*tasks, return_exceptions=True)


# --- input_mutation (R1-74) ---


async def test_input_mutation_rolls_back_rows_when_the_body_fails(client, db_session, migrated_db):
    _, _, pid_u, owner_id = await _project_ids(client, db_session)
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as s:
            with pytest.raises(RuntimeError):
                async with input_mutation(s, pid_u) as m:
                    s.add(
                        AuditLog(actor_id=owner_id, action="x.test", target_type="t", target_id="1")
                    )

                    def boom() -> None:
                        raise RuntimeError("fs step failed")

                    await m.apply(boom)
            count = (
                await s.execute(select(func.count()).where(AuditLog.action == "x.test"))
            ).scalar_one()
    finally:
        await engine.dispose()
    assert count == 0


async def test_input_mutation_runs_the_fs_step_only_after_the_rows_flush(
    client, db_session, migrated_db
):
    """A row the database refuses must stop the filesystem step: flush first."""
    _, _, pid_u, _ = await _project_ids(client, db_session)
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    ran = []
    try:
        async with factory() as s:
            with pytest.raises(Exception):  # noqa: B017  (IntegrityError via the driver)
                async with input_mutation(s, pid_u) as m:
                    # project_files.project_id has a FK: an unknown project fails at flush
                    s.add(ProjectFile(project_id=uuid.uuid4(), name="ghost.md"))
                    await m.apply(lambda: ran.append(True))
    finally:
        await engine.dispose()
    assert ran == []


# --- settings (R1-04, R1-05) ---


async def test_two_settings_writers_with_one_hash_cannot_both_win(client, db_session, migrated_db):
    """R1-04: both writers pass the early hash check; the one that commits
    second must get a conflict, not silently overwrite the first."""
    _, _, pid_u, owner_id = await _project_ids(client, db_session)
    project = await db_session.get(Project, pid_u)
    content, expected = settings_service.read_settings(project)

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as s0, factory() as s1, factory() as s2:
            await lock_project(s0, pid_u)
            a = asyncio.create_task(
                settings_service.write_settings(
                    s1, await s1.get(Project, pid_u), content + "\n# a\n", expected, owner_id
                )
            )
            b = asyncio.create_task(
                settings_service.write_settings(
                    s2, await s2.get(Project, pid_u), content + "\n# b\n", expected, owner_id
                )
            )
            results = await _race(s0, a, b)
    finally:
        await engine.dispose()

    conflicts = [r for r in results if isinstance(r, settings_service.SettingsConflictError)]
    wins = [r for r in results if isinstance(r, str)]
    assert len(conflicts) == 1 and len(wins) == 1, results
    assert settings_service.read_settings(project)[1] == wins[0]


async def test_a_settings_write_whose_rows_fail_leaves_the_file_untouched(client, db_session):
    """R1-05: rows flush before the file is written, so a database refusal
    (here an unknown actor for settings_versions.saved_by) changes nothing."""
    _, _, pid_u, _ = await _project_ids(client, db_session)
    project = await db_session.get(Project, pid_u)
    content, expected = settings_service.read_settings(project)

    with pytest.raises(Exception):  # noqa: B017  (IntegrityError via the driver)
        await settings_service.write_settings(
            db_session, project, content + "\n# lost\n", expected, uuid.uuid4()
        )
    # the rollback expired `project`; re-fetch rather than lazy-load it
    project = await db_session.get(Project, pid_u)
    assert settings_service.read_settings(project) == (content, expected)


# --- .env (R1-06) ---


async def test_concurrent_env_writes_on_different_keys_both_land(client, db_session, migrated_db):
    """R1-06: the .env is read inside the lock, so the second writer sees
    the first writer's key instead of overwriting it with a stale snapshot."""
    _, _, pid_u, owner_id = await _project_ids(client, db_session)
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as s0, factory() as s1, factory() as s2:
            await lock_project(s0, pid_u)
            a = asyncio.create_task(
                env_service.set_env_key(s1, await s1.get(Project, pid_u), "KEY_A", "a", owner_id)
            )
            b = asyncio.create_task(
                env_service.set_env_key(s2, await s2.get(Project, pid_u), "KEY_B", "b", owner_id)
            )
            results = await _race(s0, a, b)
    finally:
        await engine.dispose()

    assert results == [None, None], results
    keys = {e["key"] for e in env_service.list_env(await db_session.get(Project, pid_u))}
    assert {"KEY_A", "KEY_B"} <= keys


# --- upload quota (R2-28) ---


async def test_concurrent_uploads_cannot_both_pass_the_quota(
    client, db_session, migrated_db, monkeypatch
):
    """R2-28: usage is re-measured inside the lock, so of two uploads that
    each fit alone but not together, exactly one lands."""
    monkeypatch.setenv("PROJECT_QUOTA_MB", "1")
    get_settings.cache_clear()
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    chunk = b"x" * (700 * 1024)
    try:
        _, _, pid_u, owner_id = await _project_ids(client, db_session)
        async with factory() as s0, factory() as s1, factory() as s2:
            await lock_project(s0, pid_u)
            a = asyncio.create_task(
                files_service.save_file(
                    s1, await s1.get(Project, pid_u), "a.md", _Bytes(chunk), owner_id
                )
            )
            b = asyncio.create_task(
                files_service.save_file(
                    s2, await s2.get(Project, pid_u), "b.md", _Bytes(chunk), owner_id
                )
            )
            results = await _race(s0, a, b)
    finally:
        await engine.dispose()
        get_settings.cache_clear()

    assert sum(isinstance(r, QuotaExceededError) for r in results) == 1, results
    landed = [p.name for p in (ws_path(pid_u) / "input").iterdir()]
    assert len(landed) == 1 and not landed[0].startswith(".")


async def test_an_in_flight_upload_does_not_count_against_the_quota(client, db_session):
    """The in-lock measure skips other uploads' tmp files: bytes still
    streaming are not stored yet."""
    _, _, pid_u, _ = await _project_ids(client, db_session)
    project = await db_session.get(Project, pid_u)
    input_dir = ws_path(pid_u) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / ".tmp-inflight").write_bytes(b"x" * 1000)
    (input_dir / "kept.md").write_bytes(b"y" * 10)
    assert await files_service.usage_bytes(project) == 10


# --- bulk delete (R1-92) ---


async def test_bulk_delete_commits_what_it_removed_and_reports_the_rest(
    client,
    db_session,
    indexed_project,  # noqa: F811  (fixture imported above)
    monkeypatch,
):
    """R1-92: one unlink failing mid-batch no longer rolls back the files
    already gone; each file's rows commit with its unlink, the failure is
    reported and its rows stay."""
    alice, pid = indexed_project
    real_unlink = type(ws_path(uuid.UUID(pid))).unlink

    def unlink(self, *a, **kw):
        if self.name == "b.md":
            raise PermissionError("denied")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(type(ws_path(uuid.UUID(pid))), "unlink", unlink)
    r = await client.post(
        f"/api/projects/{pid}/files:bulk-delete", headers=alice, json={"names": ["a.md", "b.md"]}
    )
    monkeypatch.undo()
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1, "bytes": 1, "failed": ["b.md"]}

    input_dir = ws_path(uuid.UUID(pid)) / "input"
    assert not (input_dir / "a.md").exists() and (input_dir / "b.md").exists()
    names = set(
        (
            await db_session.execute(
                select(ProjectFile.name).where(ProjectFile.project_id == uuid.UUID(pid))
            )
        ).scalars()
    )
    assert names == {"b.md"}
    deleted_audits = (
        await db_session.execute(
            select(AuditLog.payload).where(
                AuditLog.action == "file.deleted", AuditLog.target_id == pid
            )
        )
    ).scalars()
    assert [p["name"] for p in deleted_audits] == ["a.md"]


# --- questions (R2-21) ---


async def test_add_question_takes_the_project_lock(
    client,
    project_with_set,  # noqa: F811  (fixture imported above)
    migrated_db,
):
    """R2-21: the cap and MAX(position)+1 are computed inside the lock, so
    two concurrent adds get distinct positions."""
    _, pid, sid = project_with_set
    pid_u, sid_u = uuid.UUID(pid), uuid.UUID(sid)
    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    try:
        async with factory() as s0, factory() as s1, factory() as s2:
            project = await s0.get(Project, pid_u)
            owner_id = project.owner_id
            await lock_project(s0, pid_u)
            a = asyncio.create_task(
                questions_service.add_question(
                    s1, await s1.get(Project, pid_u), sid_u, "first?", owner_id
                )
            )
            b = asyncio.create_task(
                questions_service.add_question(
                    s2, await s2.get(Project, pid_u), sid_u, "second?", owner_id
                )
            )
            qa, qb = await _race(s0, a, b)
    finally:
        await engine.dispose()
    assert qa.position != qb.position


async def test_live_questions_break_position_ties_by_creation(
    client,
    db_session,
    project_with_set,  # noqa: F811  (fixture imported above)
):
    """Rows that already share a position (pre-fix races) still list in one
    stable order: position, then created_at, then id."""
    _, pid, sid = project_with_set
    project = await db_session.get(Project, uuid.UUID(pid))
    sid_u = uuid.UUID(sid)
    rows = [
        Question(
            id=uuid.UUID(int=n),
            set_id=sid_u,
            lineage_id=uuid.uuid4(),
            text=f"q{n}",
            position=0,
            created_by=project.owner_id,
        )
        for n in (2, 1)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    live = await questions_service.live_questions(db_session, project.id, sid_u)
    # same transaction → same created_at, so id decides
    assert [q.text for q in live] == ["q1", "q2"]
