"""Index snapshots (spec 5.2): what the indexer was handed, what it
produced, and how the baseline advances.

The seven-row mirror table is the point of this module. Two earlier rules
failed rows three and four: a filename-only condition pinned a repaired
document at `modified` forever, and adding "N in post" to the condition did
the same to a document that was retried and dropped again.
"""

import uuid

import pandas as pd
import pytest
import yaml

from graphrag_ui.adapters.models import (
    IndexSnapshot,
    IndexSnapshotEntry,
    Job,
    Project,
    User,
)
from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.config import get_settings
from graphrag_ui.services import files as files_service
from graphrag_ui.services import index_snapshots
from graphrag_ui.services.files import list_files
from graphrag_ui.services.projects import ws_path


async def _project(db_session) -> tuple[Project, User]:
    """Persisted owner + project with a FakeInitializer workspace (input/ +
    settings.yaml, no title_column) under the patched WORKSPACES_DIR."""
    user = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        name="snap",
        slug=f"snap-{uuid.uuid4().hex[:8]}",
        owner_id=user.id,
        input_file_type="text",
    )
    db_session.add(project)
    await db_session.flush()
    await FakeInitializer().init(ws_path(project.id), "text")
    await db_session.commit()
    return project, user


async def _job(db_session, project, type_="index", status="running") -> Job:
    root = ws_path(project.id)
    job = Job(
        project_id=project.id,
        type=type_,
        method="fast",
        argv=[type_, "--root", str(root), "--method", "fast"],
        queued_by=project.owner_id,
        status=status,
    )
    db_session.add(job)
    await db_session.commit()
    return job


def _write_documents(root, titles: list[str]) -> None:
    """What the CLI leaves behind: output/documents.parquet with one row per
    ingested document, `title` = the input filename (text reader)."""
    out = root / "output"
    out.mkdir(exist_ok=True)
    pd.DataFrame({"id": [f"d{i}" for i in range(len(titles))], "title": titles}).to_parquet(
        out / "documents.parquet"
    )


async def _states(db_session, project) -> dict[str, str]:
    await db_session.refresh(project)
    listing = await list_files(db_session, project)
    return {f["name"]: f["index_state"] for f in listing["files"]}


def _stub_hash(monkeypatch) -> None:
    """Hashes the tests can name literally: sha256_file -> "hash-<content>".
    capture_start resolves it from index_snapshots' module globals and the
    listing's _scan_input from files' — both are stubbed so a listing over
    a promoted baseline compares like with like."""
    monkeypatch.setattr(index_snapshots, "sha256_file", lambda p: f"hash-{p.read_text()}")
    monkeypatch.setattr(files_service, "sha256_file", lambda p: f"hash-{p.read_text()}")


@pytest.fixture
async def project_with_files(db_session, tmp_path, monkeypatch):
    """Persisted project whose input/ holds a.md="A", b.md="B"; yields
    (project, owner) — enqueue() writes queued_by, so the owner row stays."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    try:
        project, owner = await _project(db_session)
        input_dir = ws_path(project.id) / "input"
        (input_dir / "a.md").write_text("A")
        (input_dir / "b.md").write_text("B")
        _stub_hash(monkeypatch)
        yield project, owner
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def project_with_baseline(db_session, tmp_path, monkeypatch):
    """project_with_files plus a previous baseline {"a.md": "h1"} produced
    by a succeeded job, with input.title_column switched on afterwards —
    the "index cleanly, enable title_column, update" state; yields
    (project, old_baseline_id)."""
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    get_settings.cache_clear()
    try:
        project, _owner = await _project(db_session)
        _stub_hash(monkeypatch)
        (ws_path(project.id) / "input" / "a.md").write_text("A")
        settings_path = ws_path(project.id) / "settings.yaml"
        data = yaml.safe_load(settings_path.read_text())
        data.setdefault("input", {})["title_column"] = "title"
        settings_path.write_text(yaml.safe_dump(data, sort_keys=False))
        job = await _job(db_session, project, status="succeeded")
        baseline = IndexSnapshot(
            job_id=job.id,
            project_id=project.id,
            kind="baseline",
            attributable_titles=[],
            title_recovery="available",
            artifact_epoch=0,
        )
        db_session.add(baseline)
        await db_session.flush()
        db_session.add(IndexSnapshotEntry(snapshot_id=baseline.id, name="a.md", sha256="h1"))
        project.baseline_snapshot_id = baseline.id
        await db_session.commit()
        yield project, baseline.id
    finally:
        get_settings.cache_clear()


async def test_start_snapshot_is_written_before_the_cli_and_hashes_input(
    db_session, project_with_files
):
    project, _ = project_with_files  # input/: a.md="A", b.md="B"
    job = await _job(db_session, project)
    sid = await index_snapshots.capture_start(db_session, project.id, job.id)

    rows = await index_snapshots.entries_of(db_session, sid)
    assert set(rows) == {"a.md", "b.md"}


async def test_only_a_start_row_exists_for_a_failed_job(db_session, project_with_files):
    project, _ = project_with_files
    job = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, job.id)
    job.status = "failed"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    kinds = await index_snapshots.kinds_of(db_session, job.id)
    assert kinds == {"start"}
    await db_session.refresh(project)
    assert project.baseline_snapshot_id is None


async def test_index_promotes_the_start_snapshot_wholesale(db_session, project_with_files):
    project, _ = project_with_files
    job = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, job.id)
    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    await db_session.refresh(project)
    assert project.baseline_snapshot_id is not None
    assert await index_snapshots.baseline_entries(db_session, project.id) == {
        "a.md": "hash-A",
        "b.md": "hash-B",
    }


async def test_update_with_no_previous_baseline_writes_no_baseline(db_session, project_with_files):
    """Nothing is learned: with no prior state, post-run attributability
    cannot say what THIS run ingested (spec 5.2c)."""
    project, _ = project_with_files
    job = await _job(db_session, project, type_="update")
    await index_snapshots.capture_start(db_session, project.id, job.id)
    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    assert await index_snapshots.kinds_of(db_session, job.id) == {"start"}
    await db_session.refresh(project)
    assert project.baseline_snapshot_id is None


@pytest.mark.parametrize(
    ("case", "prev_baseline", "start", "pre", "post", "expected"),
    [
        (
            "content changed, same name -> not re-ingested, old hash kept",
            {"a.md": "h1"},
            {"a.md": "h2"},
            {"a.md"},
            {"a.md"},
            {"a.md": "h1"},
        ),
        (
            "deleted -> still in the index, entry kept",
            {"a.md": "h1"},
            {},
            {"a.md"},
            {"a.md"},
            {"a.md": "h1"},
        ),
        (
            "previously skipped, now fixed -> ingested, advances",
            {"a.md": "h1"},
            {"a.md": "h2"},
            set(),
            {"a.md"},
            {"a.md": "h2"},
        ),
        (
            "previously skipped, edited, dropped again -> advances anyway",
            {"a.md": "h1"},
            {"a.md": "h2"},
            set(),
            set(),
            {"a.md": "h2"},
        ),
        (
            "previously skipped, untouched -> advances (no-op)",
            {"a.md": "h1"},
            {"a.md": "h1"},
            set(),
            set(),
            {"a.md": "h1"},
        ),
        (
            "brand new, ingested -> added",
            {},
            {"n.md": "h9"},
            set(),
            {"n.md"},
            {"n.md": "h9"},
        ),
        (
            "brand new, silently dropped -> added",
            {},
            {"n.md": "h9"},
            set(),
            set(),
            {"n.md": "h9"},
        ),
    ],
)
def test_update_advancement_mirror_table(case, prev_baseline, start, pre, post, expected):
    """Pure rule, tested without the database: for each name N with hash H in
    the START snapshot, N not in the previous baseline -> add N->H; N in the
    previous baseline -> advance iff N is NOT in `pre`. Names in the previous
    baseline but absent from the start snapshot are kept (they are `removed`
    and still live in the index)."""
    assert index_snapshots.advance_entries(prev_baseline, start, pre) == expected


async def test_update_with_unavailable_recovery_keeps_entries_but_moves_the_pointer(
    db_session, project_with_baseline
):
    """The run happened and the artifacts moved, so a successful update
    ALWAYS writes a new baseline row and moves the pointer - carrying the
    entries forward verbatim while recording the NEW recovery provenance.

    The regression this guards: index cleanly, enable title_column, update -
    the listing must stop reporting `available`."""
    project, old_baseline_id = project_with_baseline
    job = await _job(db_session, project, type_="update")
    await index_snapshots.capture_start(db_session, project.id, job.id)  # title_column set
    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    await db_session.refresh(project)
    assert project.baseline_snapshot_id != old_baseline_id
    row = await index_snapshots.baseline_row(db_session, project.id)
    assert row.title_recovery == "unavailable_title_column"
    assert await index_snapshots.baseline_entries(db_session, project.id) == {"a.md": "h1"}


async def test_artifact_epoch_moves_for_every_spawn_and_records_on_promotion(
    db_session, project_with_files
):
    project, _ = project_with_files
    assert project.artifact_epoch == 0

    job = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, job.id)
    epoch = await index_snapshots.bump_artifact_epoch(db_session, project.id)
    assert epoch == 1

    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    row = await index_snapshots.baseline_row(db_session, project.id)
    assert row.artifact_epoch == 1


async def test_a_failed_attempt_leaves_the_epoch_ahead_of_the_baseline(
    db_session, project_with_baseline
):
    """A failed job rewrites output/ in place (index_runner.py:69 spawns with
    cwd=root; there is no staging and no rollback) and promotes nothing, so
    the epoch moves and the baseline's does not. Slice 3's generation guard
    reads exactly this difference."""
    project, _ = project_with_baseline
    job = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, job.id)
    await index_snapshots.bump_artifact_epoch(db_session, project.id)
    job.status = "failed"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    await db_session.refresh(project)
    baseline = await index_snapshots.baseline_row(db_session, project.id)
    assert project.artifact_epoch != baseline.artifact_epoch


async def test_a_job_cancelled_while_queued_does_not_move_the_epoch(
    db_session, project_with_files, monkeypatch
):
    """The increment is at CLI spawn, not at enqueue: the mismatch is
    permanent, so counting an attempt that touched nothing would darken every
    citation link in the project until someone ran a full index."""
    from graphrag_ui.services import jobs as jobs_service

    project, owner = project_with_files
    job = await jobs_service.enqueue(db_session, project, "index", "standard", owner)
    assert job.status == "queued"
    await db_session.refresh(project)
    assert project.artifact_epoch == 0


async def test_start_snapshot_skips_dotfile_leftovers(db_session, project_with_files):
    """A crashed upload leaves a .tmp-* dotfile in input/; listings skip
    dotfiles and so must the snapshot, or the promoted baseline would carry
    a phantom name the indexer never ingested — and the union listing would
    show it as `removed` forever."""
    project, _owner = project_with_files
    (ws_path(project.id) / "input" / ".tmp-leftover").write_bytes(b"junk")
    job = await _job(db_session, project)
    sid = await index_snapshots.capture_start(db_session, project.id, job.id)

    rows = await index_snapshots.entries_of(db_session, sid)
    assert set(rows) == {"a.md", "b.md"}


async def test_index_promotes_the_post_run_title_set_not_the_pre_run_one(
    db_session, project_with_files
):
    """R1-67 / R3-01: on a fresh project the start snapshot is captured
    before the CLI runs, when there is no documents.parquet at all, so its
    attributable set is empty. The baseline must carry the set recovered
    from the parquet the run PRODUCED (spec 5.2c / 6.3), or every first
    index reports every file `skipped`."""
    project, _ = project_with_files  # input/: a.md, b.md; no output/ yet
    job = await _job(db_session, project)
    sid = await index_snapshots.capture_start(db_session, project.id, job.id)
    start = await db_session.get(IndexSnapshot, sid)
    assert start.attributable_titles == []  # pre-run: nothing indexed yet

    _write_documents(ws_path(project.id), ["a.md"])  # b.md silently dropped
    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    baseline = await index_snapshots.baseline_row(db_session, project.id)
    assert baseline.title_recovery == "available"
    assert baseline.attributable_titles == ["a.md"]
    assert await _states(db_session, project) == {"a.md": "indexed", "b.md": "skipped"}
    # The start row keeps the pre-run set: advance_entries needs it as `pre`.
    await db_session.refresh(start)
    assert start.attributable_titles == []


async def test_index_with_every_document_ingested_reports_everything_indexed(
    db_session, project_with_files
):
    """The happy path the quickstart reproduced as all-skipped."""
    project, _ = project_with_files
    job = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, job.id)
    _write_documents(ws_path(project.id), ["a.md", "b.md"])
    job.status = "succeeded"
    await index_snapshots.promote(db_session, job)
    await db_session.commit()

    assert await _states(db_session, project) == {"a.md": "indexed", "b.md": "indexed"}


async def test_update_promotes_the_titles_of_the_merged_output(db_session, project_with_files):
    """An update's baseline must attribute the documents the merge landed,
    including the ones THIS run ingested — the pre-run set cannot know them,
    so copying it marks every newly added file `skipped`."""
    project, _ = project_with_files
    root = ws_path(project.id)
    first = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, first.id)
    _write_documents(root, ["a.md", "b.md"])
    first.status = "succeeded"
    await index_snapshots.promote(db_session, first)
    await db_session.commit()

    (root / "input" / "c.md").write_text("C")  # ingested by the update
    (root / "input" / "d.md").write_text("D")  # offered, dropped
    upd = await _job(db_session, project, type_="update")
    await index_snapshots.capture_start(db_session, project.id, upd.id)
    _write_documents(root, ["a.md", "b.md", "c.md"])  # merged output
    upd.status = "succeeded"
    await index_snapshots.promote(db_session, upd)
    await db_session.commit()

    baseline = await index_snapshots.baseline_row(db_session, project.id)
    assert baseline.attributable_titles == ["a.md", "b.md", "c.md"]
    assert await _states(db_session, project) == {
        "a.md": "indexed",
        "b.md": "indexed",
        "c.md": "indexed",
        "d.md": "skipped",
    }


async def test_update_keeps_a_removed_document_attributable(db_session, project_with_files):
    """A file deleted from input/ stays in the index after an update
    (deleted_inputs discarded, spec 5.2c); its title is still in the merged
    parquet and must stay attributable so a citation into it resolves."""
    project, _ = project_with_files
    root = ws_path(project.id)
    first = await _job(db_session, project)
    await index_snapshots.capture_start(db_session, project.id, first.id)
    _write_documents(root, ["a.md", "b.md"])
    first.status = "succeeded"
    await index_snapshots.promote(db_session, first)
    await db_session.commit()

    (root / "input" / "b.md").unlink()
    upd = await _job(db_session, project, type_="update")
    await index_snapshots.capture_start(db_session, project.id, upd.id)
    upd.status = "succeeded"
    await index_snapshots.promote(db_session, upd)  # documents.parquet still lists b.md
    await db_session.commit()

    baseline = await index_snapshots.baseline_row(db_session, project.id)
    assert baseline.attributable_titles == ["a.md", "b.md"]
    assert await _states(db_session, project) == {"a.md": "indexed", "b.md": "removed"}
