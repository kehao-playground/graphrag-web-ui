"""Shared builders for the citation guard/source tests (slice 3 Task 2).

The tests exercise the real resolution machinery — the batched resolver
over a real documents.parquet, real baseline rows, real generation reads —
against fake indexed workspaces written directly as parquet + DB rows.
Nothing here forks the graphrag CLI.

Frame shape note: these frames use the search-context shape (int ids in
"id"), which query._frame_texts and citations._hrid_to_document key the
same way; the parquet shape (hash id + human_readable_id column) is
covered by test_query_stream_sse.py and the adapter tests.
"""

import uuid
from types import SimpleNamespace

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import (
    IndexSnapshot,
    IndexSnapshotEntry,
    Job,
    Project,
    Question,
    QuestionSet,
    TestResult,
    TestRun,
    User,
)
from graphrag_ui.services import index_snapshots
from graphrag_ui.services.projects import ws_path

# The text every seeded text unit carries; the ad-hoc preview test searches
# input/ for exactly this passage.
UNIT_TEXT = "the cited passage text"


def sources_answer(*hrids: int) -> str:
    """An answer whose marker cites the given text-unit hrids."""
    ids = ", ".join(str(h) for h in hrids)
    return f"Answer body [Data: Sources ({ids})]."


def text_unit_frame(hrids: list[int], document_ids: list[str]) -> pd.DataFrame:
    """One row per text unit: hrid, its text, and its document id."""
    return pd.DataFrame(
        {
            "id": hrids,
            "text": [UNIT_TEXT] * len(hrids),
            "document_id": document_ids,
        }
    )


def entity_frame(hrid: int = 7) -> pd.DataFrame:
    """A non-Sources context frame; its entries must stay unlinked."""
    return pd.DataFrame({"id": [hrid], "name": ["Entity Seven"]})


def write_documents(root, rows: list[tuple[str, str]]) -> None:
    """output/documents.parquet as graphrag writes it: (id, title) rows."""
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"id": [r[0] for r in rows], "title": [r[1] for r in rows]}).to_parquet(
        out / "documents.parquet"
    )


def write_workspace(root) -> None:
    """Minimal workspace shell: settings.yaml for the batch revision digest."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.yaml").write_text("x: 1\n")


class FakeSearchAdapter:
    """The only search seam: a fixed answer plus a fixed context, both
    reconfigurable per fixture/test."""

    def __init__(self) -> None:
        self.answer = "Answer body [Data: Sources (1); Entities (7)]."
        self.context: dict[str, pd.DataFrame] = {}

    async def search(self, method, config, frames, query, response_type):
        return self.answer, self.context

    def stream(self, method, config, frames, query, response_type):
        async def gen():
            yield self.answer

        return gen()


class FakeFrameCache:
    """Serves every requested table from one dict of frames."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.tables: list[str] = []

    async def get(self, root, table):
        self.tables.append(table)
        return self.frames.get(table, pd.DataFrame())


async def seed_project(db_session: AsyncSession) -> tuple[Project, User]:
    """Persisted owner + project; the workspace is created by the caller."""
    user = User(email=f"u{uuid.uuid4().hex[:6]}@t.local", password_hash="x", display_name="u")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        name="cites", slug=f"cites-{uuid.uuid4().hex[:8]}", owner_id=user.id, input_file_type="text"
    )
    db_session.add(project)
    await db_session.flush()
    return project, user


async def promote_baseline(
    db_session: AsyncSession,
    project: Project,
    entries: list[str],
    *,
    epoch: int,
    recovery: str = "available",
) -> IndexSnapshot:
    """The rows a successful index leaves behind: a succeeded job plus a
    promoted baseline at `epoch` with its entry names, and the project
    pointer moved. Seeded as rows instead of forking the real CLI."""
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
        attributable_titles=[],
        title_recovery=recovery,
        artifact_epoch=epoch,
    )
    db_session.add(snap)
    await db_session.flush()
    db_session.add_all(
        IndexSnapshotEntry(snapshot_id=snap.id, name=name, sha256=f"sha-{name}") for name in entries
    )
    project.baseline_snapshot_id = snap.id
    project.artifact_epoch = epoch
    await db_session.commit()
    return snap


async def _advance_epoch(db_session: AsyncSession, project: Project) -> int:
    """The runner's spawn-time increment, through the real code path. The
    UPDATE does not sync this ORM instance, so the attribute is set by hand."""
    epoch = await index_snapshots.bump_artifact_epoch(db_session, project.id)
    project.artifact_epoch = epoch
    return epoch


async def _promote_new_baseline(
    db_session: AsyncSession,
    project: Project,
    entries: list[str],
    documents: list[tuple[str, str]] | None = None,
) -> None:
    """A LATER successful index: epoch bump, fresh documents.parquet, new
    promoted baseline, pointer moved."""
    epoch = await _advance_epoch(db_session, project)
    if documents is not None:
        write_documents(ws_path(project.id), documents)
    await promote_baseline(db_session, project, entries, epoch=epoch)


async def _start_index_job(db_session: AsyncSession, project: Project) -> None:
    """A queued index job that never runs; G1 must see the ACTIVE JOB."""
    db_session.add(
        Job(
            project_id=project.id,
            type="index",
            method="fast",
            argv=["index", "--root", str(ws_path(project.id)), "--method", "fast"],
            queued_by=project.owner_id,
            status="queued",
        )
    )
    await db_session.commit()


async def _run_index_to_failure(
    db_session: AsyncSession, project: Project, *, job_row: bool = True
) -> None:
    """Spawn (epoch bump) -> rewrite documents.parquet -> fail, promote
    nothing: the wreckage only artifact_epoch distinguishes (spec 7.4).
    The job row is optional because the batch barrier test fires this
    while the run's own test_run job holds jobs_one_active_per_project —
    the guard reads the epoch, not the row."""
    job: Job | None = None
    if job_row:
        job = Job(
            project_id=project.id,
            type="index",
            method="fast",
            argv=["index", "--root", str(ws_path(project.id)), "--method", "fast"],
            queued_by=project.owner_id,
            status="running",
        )
        db_session.add(job)
        await db_session.flush()
    await _advance_epoch(db_session, project)
    write_documents(ws_path(project.id), [("d1", "wreckage")])
    if job is not None:
        job.status = "failed"
    await db_session.commit()


async def _run_index_to_success(
    db_session: AsyncSession,
    project: Project,
    entries: list[str],
    documents: list[tuple[str, str]],
) -> None:
    """Spawn -> rewrite documents.parquet cleanly -> succeed and promote:
    the only transition that may turn links back on."""
    epoch = await _advance_epoch(db_session, project)
    write_documents(ws_path(project.id), documents)
    await promote_baseline(db_session, project, entries, epoch=epoch)


async def _rebuild_so_that_hrid_1_is(db_session: AsyncSession, project: Project, name: str) -> None:
    """A full re-index that reassigns hrid 1's document to `name`: a fresh
    promotion the guard itself would accept, so a resolution that wrongly
    consulted CURRENT artifacts would return `name`."""
    await _run_index_to_success(db_session, project, [name], [("d1", name)])


def _all_source_names_null(body: dict) -> bool:
    return all(
        entry["source_name"] is None
        for c in body["citations"]
        if c["label"].strip().lower() in ("sources", "source")
        for entry in c["entries"]
    )


async def seed_test_run(
    db_session: AsyncSession,
    *,
    questions: int,
    hrids: list[int],
    document_ids: list[str],
    documents: list[tuple[str, str]],
    entries: list[str],
) -> SimpleNamespace:
    """A queued test_run job + run + one placeholder per question over a
    fake indexed workspace; every answer cites the given hrids. Executable
    by the real worker once the query seams are faked by the caller."""
    project, user = await seed_project(db_session)
    root = ws_path(project.id)
    write_workspace(root)
    write_documents(root, documents)
    await promote_baseline(db_session, project, entries, epoch=1)
    qs = QuestionSet(project_id=project.id, name="Cite", created_by=user.id)
    db_session.add(qs)
    await db_session.flush()
    question_rows = [
        Question(
            set_id=qs.id, lineage_id=uuid.uuid4(), text=f"q{i}", position=i, created_by=user.id
        )
        for i in range(questions)
    ]
    db_session.add_all(question_rows)
    await db_session.flush()
    job = Job(
        project_id=project.id,
        type="test_run",
        method="local",
        argv=[],
        queued_by=user.id,
        status="queued",
    )
    db_session.add(job)
    await db_session.flush()
    run = TestRun(project_id=project.id, set_id=qs.id, job_id=job.id, method="local")
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        TestResult(run_id=run.id, question_id=q.id, position=i, question_text=q.text)
        for i, q in enumerate(question_rows)
    )
    # Plain JSONB columns without change tracking: assign, never mutate.
    job.params = {"run_id": str(run.id)}
    job.progress = {"done": 0, "total": len(question_rows)}
    await db_session.commit()
    return SimpleNamespace(job_id=job.id, run_id=run.id, root=root, project_id=project.id)
