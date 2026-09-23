"""Input freeze (spec 5.2b): upload/delete/settings/.env are refused while
an index or update job holds the project, and the lock - not the check -
is what makes the start snapshot the indexer's fixed input.

The barrier tests matter more than the 409 tests. A test that only asserts
"active job -> 409" passes against the broken check-then-act design; only
an interleaving distinguishes them, so two of them are written by hand with
explicit park points.
"""

import asyncio
import os
import string
import uuid
from typing import Any

import pytest
import yaml

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import Job, Project, User
from graphrag_ui.domain.artifacts import title_column_configured
from graphrag_ui.services import files as files_service
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services import settings as settings_service
from graphrag_ui.services.errors import ProjectIndexingError
from graphrag_ui.services.project_lock import lock_project
from graphrag_ui.services.projects import ws_path
from tests.test_files import (
    _alice,
    _make_project,
    _upload,
    indexed_project,  # noqa: F401  (pytest fixture; test params shadow it)
)


def monkeypatch_attr(module, name, value):
    """Install a barrier seam: setattr through module globals, the same
    mechanism runner_loop uses for IndexRunner."""
    setattr(module, name, value)


def _effective_settings(project: Project) -> Any:
    """settings.yaml rendered the way graphrag loads it: strict Template
    substitution over os.environ overlaid by the workspace .env, then YAML
    parse — the same order services/settings.py validates writes with."""
    env = dict(os.environ)
    env_path = ws_path(project.id) / ".env"
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    rendered = string.Template(settings_service.read_settings(project)[0]).substitute(env)
    return yaml.safe_load(rendered)


class _Bytes:
    """Minimal async reader with the UploadFile.read(n) shape."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, n: int) -> bytes:
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk


async def _queue_index_job(db_session, project_id, user_id, type_="index"):
    job = Job(
        project_id=project_id,
        type=type_,
        method="standard",
        argv=[],
        queued_by=user_id,
        status="queued",
    )
    db_session.add(job)
    await db_session.commit()
    return job


async def test_upload_and_delete_are_refused_while_indexing(client, db_session):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    assert (await _upload(client, alice, pid, "a.md", b"x")).status_code == 201

    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id)

    r = await _upload(client, alice, pid, "b.md", b"y")
    assert r.status_code == 409
    assert r.json()["code"] == "project_indexing"

    r = await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)
    assert r.status_code == 409
    assert r.json()["code"] == "project_indexing"


async def test_settings_and_env_are_refused_while_indexing(client, db_session):
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))

    current = (await client.get(f"/api/projects/{pid}/settings", headers=alice)).json()
    await _queue_index_job(db_session, project.id, project.owner_id, type_="update")

    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=alice,
        json={"content": current["content"], "expected_hash": current["content_hash"]},
    )
    assert r.status_code == 409 and r.json()["code"] == "project_indexing"

    r = await client.patch(
        f"/api/projects/{pid}/env", headers=alice, json={"key": "K", "value": "v"}
    )
    assert r.status_code == 409 and r.json()["code"] == "project_indexing"

    r = await client.delete(f"/api/projects/{pid}/env/K", headers=alice)
    assert r.status_code == 409 and r.json()["code"] == "project_indexing"


async def test_a_test_run_job_does_not_freeze_document_work(client, db_session):
    """The freeze predicate is index/update only: a test_run reads output/
    and blocking uploads for it would be ceremony without a reason."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id, type_="test_run")

    assert (await _upload(client, alice, pid, "b.md", b"y")).status_code == 201


async def test_upload_parked_before_commit_loses_to_enqueue(client, db_session, migrated_db):
    """Barrier 1: an upload that has finished streaming but not yet opened
    its committing transaction must, once released, find the job and 409 -
    and its bytes must never reach input/.

    This is the interleaving a bare check-then-act loses. A test that only
    asserts "active job -> 409" passes against the broken design.
    """
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    pid_u, owner_id = project.id, project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    parked = asyncio.Event()
    release = asyncio.Event()
    original = files_service._commit_upload

    async def parking_commit(*args, **kwargs):
        parked.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch_attr(files_service, "_commit_upload", parking_commit)
    try:
        async with factory() as s1, factory() as s2:
            owner = await s2.get(User, owner_id)
            uploader = asyncio.create_task(
                files_service.save_file(
                    s1,
                    await s1.get(Project, pid_u),
                    "late.md",
                    _Bytes(b"late"),
                    actor_id=owner_id,
                )
            )
            await asyncio.wait_for(parked.wait(), timeout=5)
            await jobs_service.enqueue(s2, await s2.get(Project, pid_u), "index", "standard", owner)
            release.set()
            with pytest.raises(ProjectIndexingError):
                await uploader
    finally:
        monkeypatch_attr(files_service, "_commit_upload", original)
        await engine.dispose()

    assert not (ws_path(pid_u) / "input" / "late.md").exists()


async def test_enqueue_waits_for_an_upload_holding_the_lock(client, db_session, migrated_db):
    """Barrier 2, the mirror: an upload holding the lock mid-rename makes
    enqueue BLOCK until it commits, so the start snapshot sees the new file.

    The seam is async on purpose: _replace_into_input is sync, so the park
    point is _commit_upload's post-rename half, reached while the row lock
    is still held.
    """
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    pid_u, owner_id = project.id, project.owner_id

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    holding = asyncio.Event()
    release = asyncio.Event()
    original = files_service._commit_upload

    async def parking_commit(session, *args, **kwargs):
        # Take the lock and rename first, then park BEFORE commit: the row
        # lock is held for the whole park, which is what enqueue must wait on.
        await lock_project(session, pid_u)
        holding.set()
        await release.wait()
        return await original(session, *args, **kwargs)

    monkeypatch_attr(files_service, "_commit_upload", parking_commit)
    try:
        async with factory() as s1, factory() as s2:
            owner = await s2.get(User, owner_id)
            uploader = asyncio.create_task(
                files_service.save_file(
                    s1,
                    await s1.get(Project, pid_u),
                    "early.md",
                    _Bytes(b"early"),
                    actor_id=owner_id,
                )
            )
            await asyncio.wait_for(holding.wait(), timeout=5)

            enqueuer = asyncio.create_task(
                jobs_service.enqueue(s2, await s2.get(Project, pid_u), "index", "standard", owner)
            )
            # It must NOT be able to finish while the upload holds the lock.
            done, _ = await asyncio.wait({enqueuer}, timeout=1.0)
            assert done == set(), "enqueue did not wait for the project lock"

            release.set()
            await uploader
            job = await asyncio.wait_for(enqueuer, timeout=5)
    finally:
        monkeypatch_attr(files_service, "_commit_upload", original)
        await engine.dispose()

    assert (ws_path(pid_u) / "input" / "early.md").exists()
    # The start snapshot the runner will capture must therefore see it.
    assert job.status == "queued"


async def test_a_settings_write_parked_before_commit_loses_to_enqueue(
    client, db_session, migrated_db
):
    """The config-freeze barrier, the same shape as barrier 1: a write
    parked before its commit cannot land after enqueue captured the start
    snapshot."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    pid_u, owner_id = project.id, project.owner_id
    content, expected = settings_service.read_settings(project)

    engine = make_engine(migrated_db)
    factory = make_session_factory(engine)
    parked, release = asyncio.Event(), asyncio.Event()
    original = settings_service._commit_settings

    async def parking(*args, **kwargs):
        parked.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch_attr(settings_service, "_commit_settings", parking)
    try:
        async with factory() as s1, factory() as s2:
            owner = await s2.get(User, owner_id)
            writer = asyncio.create_task(
                settings_service.write_settings(
                    s1,
                    await s1.get(Project, pid_u),
                    content + "\n# edited\n",
                    expected,
                    owner_id,
                )
            )
            await asyncio.wait_for(parked.wait(), timeout=5)
            await jobs_service.enqueue(s2, await s2.get(Project, pid_u), "index", "standard", owner)
            release.set()
            with pytest.raises(ProjectIndexingError):
                await writer
    finally:
        monkeypatch_attr(settings_service, "_commit_settings", original)
        await engine.dispose()

    assert settings_service.read_settings(await db_session.get(Project, pid_u))[1] == expected


async def test_env_alone_moves_the_effective_configuration(client, db_session):
    """The .env case needs its OWN test: a settings.yaml referencing
    ${TITLE_COLUMN} changes meaning through .env alone, invisibly, which is
    why freezing settings.yaml is not enough (spec 6.3)."""
    alice = await _alice(client)
    pid = await _make_project(client, alice, input_file_type="csv")
    project = await db_session.get(Project, uuid.UUID(pid))

    await client.patch(
        f"/api/projects/{pid}/env", headers=alice, json={"key": "TITLE_COLUMN", "value": "name"}
    )
    content, h = settings_service.read_settings(project)
    patched = content.replace("input:\n", "input:\n  title_column: ${TITLE_COLUMN}\n")
    assert (
        await client.put(
            f"/api/projects/{pid}/settings",
            headers=alice,
            json={"content": patched, "expected_hash": h},
        )
    ).status_code == 200

    # settings.yaml unchanged from here on; only .env moves.
    assert title_column_configured(_effective_settings(project)) is True
    assert (
        await client.delete(f"/api/projects/{pid}/env/TITLE_COLUMN", headers=alice)
    ).status_code in (204, 400)


# --- tags vs bulk delete under the freeze (spec 8) ---


async def test_tagging_is_allowed_while_indexing(client, db_session, indexed_project):  # noqa: F811  (fixture imported above)
    """Tags are metadata, not input (spec 8): tagging a document while an
    index runs changes nothing the indexer reads, so no freeze check —
    only the project lock, which discovery also takes."""
    alice, pid = indexed_project
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id)

    r = await client.post(
        f"/api/projects/{pid}/files/a.md/tags", headers=alice, json={"tags": ["x"]}
    )
    assert r.status_code == 204


async def test_bulk_delete_is_refused_while_indexing(client, db_session, indexed_project):  # noqa: F811  (fixture imported above)
    """Bulk delete IS input: same lock, same 409 as a single delete."""
    alice, pid = indexed_project
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id)

    r = await client.post(
        f"/api/projects/{pid}/files:bulk-delete", headers=alice, json={"names": ["a.md"]}
    )
    assert r.status_code == 409 and r.json()["code"] == "project_indexing"


@pytest.mark.parametrize("job_type", ["index", "update", "test_run"])
async def test_project_delete_is_refused_while_any_job_is_active(client, db_session, job_type):
    """R1-01: removing the workspace under a running graphrag (or a batch
    reading output/) strands the job; deletion waits for it to finish."""
    alice = await _alice(client)
    pid = await _make_project(client, alice)
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id, type_=job_type)

    r = await client.delete(f"/api/projects/{pid}", headers=alice)
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"
    assert ws_path(project.id).exists()
    assert (await client.get(f"/api/projects/{pid}", headers=alice)).status_code == 200


async def test_project_delete_commits_before_removing_the_workspace(client, monkeypatch):
    """R1-01: the row goes first; a failing directory removal is logged, not
    a 500 after a half-done delete."""
    from graphrag_ui.services import projects as projects_service

    alice = await _alice(client)
    pid = await _make_project(client, alice)

    def _rmtree_fails(path, *a, **kw):
        raise OSError("directory not empty")

    monkeypatch.setattr(projects_service.shutil, "rmtree", _rmtree_fails)
    assert (await client.delete(f"/api/projects/{pid}", headers=alice)).status_code == 204
    assert (await client.get(f"/api/projects/{pid}", headers=alice)).status_code == 404
