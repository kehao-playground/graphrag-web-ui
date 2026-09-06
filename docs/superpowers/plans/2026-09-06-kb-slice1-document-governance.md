# Knowledge-Manager Slice ① — Document Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every input file carry a trustworthy index state computed from recorded evidence, and make hundreds of documents searchable, taggable, previewable and bulk-operable.

**Architecture:** A `start` index snapshot is captured from `input/` before the graphrag CLI spawns; a `baseline` snapshot is promoted in the same transaction that marks the job `succeeded`. A project-row lock (`SELECT … FROM projects WHERE id = :id FOR UPDATE`) serializes file/settings/`.env` mutation against job enqueue, so the snapshot really is the indexer's fixed input. Per-file state is then a pure function of (current sha256, baseline entries, attributable titles). `documents.title → filename` recovery is evaluated when artifacts are produced and stored on the baseline row, never re-derived at read time.

**Tech Stack:** FastAPI + pydantic v2, SQLAlchemy 2 async + alembic + testcontainers (Postgres 16), duckdb over parquet, React 19 + TS + antd 6 + vitest, openapi-typescript codegen.

**Spec:** `docs/superpowers/specs/2026-09-06-knowledge-manager-ux-design.md` — the spec travels with this plan; executors read both. Section references (§5.2, §6.3…) point at the spec.

## Global Constraints

- **Layering** (AGENTS.md, CI-enforced): `domain/` pure — no I/O, no fastapi/sqlalchemy/graphrag imports; `services/` no FastAPI imports and no `HTTPException` — they raise domain errors that routes translate, and they own the transaction boundary (`audit()` adds, services commit; `flush → external work → commit` with rollback on failure); `adapters/` owns Postgres repos, FS workspace and every graphrag touchpoint; `api/` owns routes/schemas/auth.
- **No new environment variables.** The fixed list in AGENTS.md is complete. Every bound in this slice is a module-level domain constant: `PREVIEW_WINDOW_BYTES = 64 * 1024`, `PASSAGE_MAX_BYTES = 4096`.
- **DB schema changes go through alembic only**; `adapters/db.py` engine stays lazy (never build engines at module import time). The schema-drift gate `tests/test_schema_drift.py` must be green at the end of every task: `adapters/models.py` must match alembic head exactly.
- **Current alembic head is `a3d81f0c6b52`** (`lowercase_user_emails`). Verify with `cd backend && uv run alembic heads` before generating a revision.
- **Permission atoms are fixed** (`domain/permissions.py`): reading is `project:view`, curating content (files, tags) is `project:edit_content`, configuration is `project:edit_settings`, spending compute is `project:run_jobs`. Frontend gating reads backend-computed `ProjectOut.my_permissions` only — never a frontend-rebuilt role table.
- **New error code: `project_indexing`** (HTTP 409), returned by upload, delete, bulk delete, `PUT .../settings`, `PATCH .../env` and `DELETE .../env/{key}` while an `index`/`update` job is `queued` or `running`. It needs a zh-TW **and** an en-US entry in the frontend error catalog (Task 8).
- **The freeze predicate is `index`/`update` only.** A `test_run` job (slice ②) reads `output/` and must never freeze document work. Write the predicate against a type list from the start so slice ② needs no change here.
- **graphrag stays pinned `==3.1.0`.** The title-recovery rule is read off `graphrag_input/text.py` and `graphrag_input/structured_file_reader.py:48-53`; it tracks an implementation, not a contract.
- **Contract gate**: `openapi.json` + `frontend/src/api/types.generated.ts` regenerate in the SAME commit as any schema/route change (`cd backend && uv run python scripts/gen_openapi.py`, then `cd frontend && npm run gen:types`). Generated files are never hand-edited.
- **Every task ends green**: `cd backend && uv run pytest -q -m "not slow"` (Docker required for testcontainers), `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`. Frontend tasks additionally `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`. No task leaves a suite red.
- Comments/docstrings **English only** (CI-enforced by `backend/tests/test_comment_language.py`); when editing a file that still has zh-TW comments, migrate the comments in the sections you touch only. Conventional Commits, English subject and body. Every new UI string lands in **both** `zh-TW` and `en-US` in the same commit (`i18n.test.ts` enforces parity).
- **Deliberate deviation from spec §8, stated up front:** `POST /api/projects/{id}/files/{name}/preview` ships in this slice with the **ad-hoc `{passage}` form only**. The historic `{result_id, entry_id}` form and its three authorization bindings are §7.4, which belongs to slice ③ and needs `test_results` rows that do not exist yet. Task 7's 422 test therefore rejects `result_id`/`entry_id` as unknown fields today; slice ③ widens it.

## File Structure

```
backend/src/graphrag_ui/
  domain/
    files.py                # NEW (Task 2): FileIndexState, AttributableTitles, index_state()
    artifacts.py            # Task 2: + recover_filenames(), title_column_configured()
  services/
    project_lock.py         # NEW (Task 3): lock_project(), assert_input_unfrozen(), FREEZING_JOB_TYPES
    errors.py               # Task 3: + ProjectIndexingError
    files.py                # Task 3: locked save/delete; Task 4: sha256_file;
                            # Task 5: list_files rewrite + discovery;
                            # Task 6: tags + bulk_delete; Task 7: preview_file
    settings.py             # Task 3: write_settings takes the lock
    env_file.py             # Task 3: set_env_key / delete_env_key take the lock
    index_snapshots.py      # NEW (Task 4): capture_start, bump_artifact_epoch, promote
    runner_loop.py          # Task 4: snapshot + epoch around the CLI spawn
    retention.py            # Task 4: prune superseded start snapshots
  adapters/
    models.py               # Task 1: ProjectFile, FileTag, FileTagLink, IndexSnapshot,
                            # IndexSnapshotEntry, Project.baseline_snapshot_id/artifact_epoch
    artifacts.py            # Task 4: read_document_titles()
    jobs_repo.py            # Task 4: finish(..., on_before_commit=)
  api/
    files_routes.py         # Task 5: FileEntryOut widening + ingest_check;
                            # Task 6: tags + bulk-delete routes; Task 7: preview routes
    settings_routes.py      # Task 3: 409 project_indexing
    env_routes.py           # Task 3: 409 project_indexing
backend/migrations/versions/
    <rev>_kb_document_governance.py   # NEW (Task 1)
backend/tests/
    test_kb_schema.py           # NEW (Task 1)
    test_domain_files.py        # NEW (Task 2)
    test_domain_artifacts.py    # Task 2: + recovery-rule cases
    test_project_freeze.py      # NEW (Task 3): 409s + both barrier interleavings
    test_index_snapshots.py     # NEW (Task 4): capture, epoch, advancement mirror table
    test_files.py               # Task 5-7: listing, discovery, tags, bulk, preview
    test_retention.py           # Task 4: snapshot pruning
    test_real_corpus_titles.py  # NEW (Task 4): slow, pins the recovery rule upstream
frontend/src/
  components/
    FilesPanel.tsx          # Task 8: becomes the container (toolbar + table + drawer)
    files/FilesToolbar.tsx  # NEW (Task 8)
    files/FilesTable.tsx    # NEW (Task 8)
    files/FilePreviewDrawer.tsx  # NEW (Task 9)
    files/indexState.tsx    # NEW (Task 8): state → {tag color, label, sentence}
  api/types.ts              # Task 8: FileEntry/FilesOut flow from codegen; IndexState union
  i18n/locales/{zh-TW,en-US}.ts   # Tasks 8-9
```

---

### Task 1: Schema — document metadata and index snapshots

**Files:**
- Create: `backend/migrations/versions/<rev>_kb_document_governance.py` (generate with `cd backend && uv run alembic revision -m "kb document governance"`; confirm `uv run alembic heads` shows `a3d81f0c6b52` first)
- Modify: `backend/src/graphrag_ui/adapters/models.py`
- Test: `backend/tests/test_kb_schema.py` (new file)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: ORM models `ProjectFile`, `FileTag`, `FileTagLink`, `IndexSnapshot`, `IndexSnapshotEntry`; `Project.baseline_snapshot_id: Mapped[uuid.UUID | None]` and `Project.artifact_epoch: Mapped[int]`. Column names are exactly as in spec §5.1/§5.2 and are relied on by every later task.

**The one non-obvious hazard: `projects` ↔ `index_snapshots` is a circular foreign key.** `index_snapshots.project_id → projects.id` and `projects.baseline_snapshot_id → index_snapshots.id` form a cycle, and `Base.metadata.sorted_tables` — which `tests/conftest.py::clean_db` uses to build its `TRUNCATE` list — raises `CircularDependencyError` on a cycle. Break it with `use_alter=True` on the `projects.baseline_snapshot_id` ForeignKey so SQLAlchemy emits it as a separate `ALTER TABLE` and drops it from the sort graph. Without this, **every test in the suite fails at fixture setup**, not just this task's.

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/test_kb_schema.py`:

```python
"""Schema shape for slice 1 (spec 5.1/5.2): the constraints later tasks
rely on, asserted against the migrated database rather than the models.

test_schema_drift.py already proves models and alembic head agree; this
module pins the three facts that a drift check cannot see - the unique
keys and the artifact_epoch default - because a missing unique key turns
a correctness bug into silent duplicate rows.
"""

import sqlalchemy as sa

from graphrag_ui.adapters.db import make_engine


async def _indexes(migrated_db, table: str) -> dict[str, tuple[bool, list[str]]]:
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            rows = await conn.run_sync(
                lambda c: sa.inspect(c).get_indexes(table)
            )
            uniques = await conn.run_sync(
                lambda c: sa.inspect(c).get_unique_constraints(table)
            )
    finally:
        await engine.dispose()
    out = {i["name"]: (bool(i["unique"]), list(i["column_names"])) for i in rows}
    out.update({u["name"]: (True, list(u["column_names"])) for u in uniques})
    return out


async def test_project_files_unique_per_project_name(migrated_db):
    idx = await _indexes(migrated_db, "project_files")
    assert any(
        unique and cols == ["project_id", "name"] for unique, cols in idx.values()
    ), idx


async def test_index_snapshots_unique_per_job_kind(migrated_db):
    idx = await _indexes(migrated_db, "index_snapshots")
    assert any(
        unique and cols == ["job_id", "kind"] for unique, cols in idx.values()
    ), idx


async def test_projects_artifact_epoch_defaults_to_zero(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: sa.inspect(c).get_columns("projects"))
    finally:
        await engine.dispose()
    epoch = next(c for c in cols if c["name"] == "artifact_epoch")
    assert epoch["nullable"] is False
    assert "0" in str(epoch["default"])
    baseline = next(c for c in cols if c["name"] == "baseline_snapshot_id")
    assert baseline["nullable"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_kb_schema.py -q`
Expected: FAIL — `NoSuchTableError: project_files`.

- [ ] **Step 3: Write the migration**

Generate the revision file, then replace its body:

```python
"""kb document governance: project files, tags, index snapshots

Revision ID: <rev>
Revises: a3d81f0c6b52
Create Date: 2026-09-06

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "<rev>"
down_revision = "a3d81f0c6b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        # Provenance is nullable because migration cannot invent it: files
        # that predate this release are discovered from input/, and nothing
        # on disk records who uploaded them (spec 5.1).
        sa.Column(
            "uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "name", name="uq_project_files_project_name"),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])

    op.create_table(
        "file_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_file_tags_project_name"),
    )

    op.create_table(
        "file_tag_links",
        sa.Column(
            "file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("project_files.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("file_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "index_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),  # start | baseline
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Filenames recovered from documents.parquet AT CAPTURE TIME, and the
        # provenance of that recovery. An empty list with recovery
        # 'available' means the rule ran and matched nothing; that is a
        # different fact from 'the rule could not run' (spec 6.3).
        sa.Column("attributable_titles", JSONB, nullable=False, server_default="[]"),
        sa.Column("title_recovery", sa.String(32), nullable=False),
        # projects.artifact_epoch at promotion; the generation guard asserts
        # equality against it (spec 7.4, slice 3).
        sa.Column("artifact_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("job_id", "kind", name="uq_index_snapshots_job_kind"),
    )
    op.create_index("ix_index_snapshots_project_id", "index_snapshots", ["project_id"])

    op.create_table(
        "index_snapshot_entries",
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("index_snapshots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
    )

    op.add_column(
        "projects", sa.Column("baseline_snapshot_id", UUID(as_uuid=True), nullable=True)
    )
    # Added after both tables exist: projects <-> index_snapshots is a cycle.
    op.create_foreign_key(
        "fk_projects_baseline_snapshot",
        "projects",
        "index_snapshots",
        ["baseline_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "projects",
        sa.Column("artifact_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("projects", "artifact_epoch")
    op.drop_constraint("fk_projects_baseline_snapshot", "projects", type_="foreignkey")
    op.drop_column("projects", "baseline_snapshot_id")
    op.drop_table("index_snapshot_entries")
    op.drop_index("ix_index_snapshots_project_id", table_name="index_snapshots")
    op.drop_table("index_snapshots")
    op.drop_table("file_tag_links")
    op.drop_table("file_tags")
    op.drop_index("ix_project_files_project_id", table_name="project_files")
    op.drop_table("project_files")
```

- [ ] **Step 4: Mirror the schema in `adapters/models.py`**

Append to `backend/src/graphrag_ui/adapters/models.py` and add the two `Project` columns:

```python
class ProjectFile(Base):
    """Metadata for one file in input/. NOT the source of truth for
    existence - input/ on disk is (spec 5.1); a delete removes the row with
    the file, and an untracked file is discovered into a row on first list."""

    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_files_project_name"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileTag(Base):
    __tablename__ = "file_tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_file_tags_project_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(50))


class FileTagLink(Base):
    __tablename__ = "file_tag_links"
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_tags.id", ondelete="CASCADE"), primary_key=True
    )


class IndexSnapshot(Base):
    """What the indexer was handed (kind='start'), or what it produced
    (kind='baseline'). One row per (job, kind): every index/update job gets a
    start row before the CLI spawns; only a promoting job gets a baseline
    row (spec 5.2)."""

    __tablename__ = "index_snapshots"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_index_snapshots_job_kind"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attributable_titles: Mapped[list] = mapped_column(JSONB, default=list)
    title_recovery: Mapped[str] = mapped_column(String(32))
    artifact_epoch: Mapped[int] = mapped_column(Integer, default=0)


class IndexSnapshotEntry(Base):
    __tablename__ = "index_snapshot_entries"
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("index_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
```

Inside `class Project`, add — note `use_alter=True`, which is what keeps `Base.metadata.sorted_tables` (used by `conftest.clean_db`) from raising on the cycle:

```python
    baseline_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # use_alter breaks the projects <-> index_snapshots FK cycle for
        # metadata sorting; without it Base.metadata.sorted_tables raises
        # CircularDependencyError and conftest's TRUNCATE fixture dies.
        ForeignKey("index_snapshots.id", ondelete="SET NULL", use_alter=True,
                   name="fk_projects_baseline_snapshot"),
        nullable=True,
    )
    artifact_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

Extend the imports at the top of the file: `BigInteger` from `sqlalchemy`.

- [ ] **Step 5: Run the schema and drift tests**

Run: `cd backend && uv run pytest tests/test_kb_schema.py tests/test_schema_drift.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the whole fast suite**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: PASS. If `clean_db` raises `CircularDependencyError`, `use_alter=True` is missing from Step 4.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/versions backend/src/graphrag_ui/adapters/models.py backend/tests/test_kb_schema.py
git commit -m "$(cat <<'EOF'
feat(kb): add document metadata and index snapshot tables

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 2: Pure domain — index state and title recovery

**Files:**
- Create: `backend/src/graphrag_ui/domain/files.py`
- Modify: `backend/src/graphrag_ui/domain/artifacts.py`
- Test: `backend/tests/test_domain_files.py` (new), `backend/tests/test_domain_artifacts.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1 (pure domain, no ORM).
- Produces:
  - `domain/files.py`: `FileIndexState` (StrEnum: `new`, `modified`, `removed`, `indexed`, `skipped`), `IngestCheck` (StrEnum: `available`, `unavailable_no_baseline`, `unavailable_not_indexed`, `unavailable_title_column`), `AttributableTitles` (frozen dataclass with `available: bool` and `filenames: frozenset[str]`, plus classmethods `of(filenames)` and `unavailable()`), and `index_state(name, current_sha, baseline, attributable) -> FileIndexState`.
  - `domain/artifacts.py`: `recover_filename(title, candidates) -> str | None`, `recover_filenames(titles, candidates) -> frozenset[str]`, `title_column_configured(settings_data) -> bool`.

- [ ] **Step 1: Write the failing state tests**

Create `backend/tests/test_domain_files.py`:

```python
"""Per-file index state (spec 6.2/6.3), table-driven.

The four states need only the baseline and are always computable; skipped
is a refinement that exists only when title recovery was available when
the artifacts were produced.
"""

import pytest

from graphrag_ui.domain.files import AttributableTitles, FileIndexState, index_state

BASE = {"a.md": "hash-a", "b.md": "hash-b"}
AVAILABLE = AttributableTitles.of({"a.md"})
UNAVAILABLE = AttributableTitles.unavailable()


@pytest.mark.parametrize(
    ("name", "current_sha", "baseline", "attributable", "expected"),
    [
        # not in baseline -> new, regardless of anything else
        ("c.md", "hash-c", BASE, AVAILABLE, FileIndexState.new),
        ("c.md", "hash-c", BASE, UNAVAILABLE, FileIndexState.new),
        ("c.md", "hash-c", {}, AVAILABLE, FileIndexState.new),
        # in baseline, hash differs -> modified
        ("a.md", "other", BASE, AVAILABLE, FileIndexState.modified),
        # in baseline, gone from input/ -> removed (current_sha is None)
        ("a.md", None, BASE, AVAILABLE, FileIndexState.removed),
        ("b.md", None, BASE, UNAVAILABLE, FileIndexState.removed),
        # in baseline, hash matches, attributable -> indexed
        ("a.md", "hash-a", BASE, AVAILABLE, FileIndexState.indexed),
        # in baseline, hash matches, NOT attributable -> skipped
        ("b.md", "hash-b", BASE, AVAILABLE, FileIndexState.skipped),
        # recovery unavailable: skipped is unreachable, falls back to indexed
        ("b.md", "hash-b", BASE, UNAVAILABLE, FileIndexState.indexed),
        # a NULL sha from the backfill path must not read as "gone"
        ("a.md", "", BASE, AVAILABLE, FileIndexState.modified),
    ],
)
def test_index_state_table(name, current_sha, baseline, attributable, expected):
    assert index_state(name, current_sha, baseline, attributable) is expected


def test_removed_outranks_hash_comparison():
    """A removed file has no hash to compare; the rule must not fall through
    to modified just because current_sha is falsy."""
    assert index_state("a.md", None, BASE, UNAVAILABLE) is FileIndexState.removed


def test_skipped_never_emitted_when_recovery_unavailable():
    for name in BASE:
        assert (
            index_state(name, BASE[name], BASE, UNAVAILABLE) is not FileIndexState.skipped
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_domain_files.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'graphrag_ui.domain.files'`.

- [ ] **Step 3: Write `domain/files.py`**

```python
"""Per-file index state (spec 6.2/6.3). Pure: no I/O, no ORM, no graphrag.

Four states need only the baseline and are ALWAYS computable. `skipped` is
a refinement available only when a documents.title could be mapped back to
a filename when the artifacts were produced - so it is expressed as a
separate input rather than folded into the baseline, and its absence
degrades to `indexed` rather than to a screen of false alarms.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class FileIndexState(StrEnum):
    new = "new"
    modified = "modified"
    removed = "removed"
    indexed = "indexed"
    skipped = "skipped"


class IngestCheck(StrEnum):
    """Whether `skipped` can be emitted at all, and why not (spec 6.3).
    Lives on the response, not on each file row: it is a property of the
    artifacts, and repeating it per file would invite a per-file rendering."""

    available = "available"
    unavailable_no_baseline = "unavailable_no_baseline"
    unavailable_not_indexed = "unavailable_not_indexed"
    unavailable_title_column = "unavailable_title_column"


@dataclass(frozen=True)
class AttributableTitles:
    """Filenames attributable to the indexed artifacts, or the fact that no
    attribution is possible. An empty set with available=True ("the rule ran
    and matched nothing") is a different fact from available=False ("the rule
    could not run"), and only the first may produce `skipped`."""

    available: bool
    filenames: frozenset[str]

    @classmethod
    def of(cls, filenames: Iterable[str]) -> "AttributableTitles":
        return cls(True, frozenset(filenames))

    @classmethod
    def unavailable(cls) -> "AttributableTitles":
        return cls(False, frozenset())


def index_state(
    name: str,
    current_sha: str | None,
    baseline: Mapping[str, str],
    attributable: AttributableTitles,
) -> FileIndexState:
    """`current_sha` is None exactly when the file is gone from input/."""
    if name not in baseline:
        return FileIndexState.new
    if current_sha is None:
        # Checked before the hash comparison: a removed file has no hash, and
        # falling through would report `modified` for a file that is gone.
        return FileIndexState.removed
    if current_sha != baseline[name]:
        return FileIndexState.modified
    if attributable.available and name not in attributable.filenames:
        return FileIndexState.skipped
    return FileIndexState.indexed
```

- [ ] **Step 4: Run the state tests**

Run: `cd backend && uv run pytest tests/test_domain_files.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Write the failing recovery tests**

Append to `backend/tests/test_domain_artifacts.py`:

```python
from graphrag_ui.domain.artifacts import (
    recover_filename,
    recover_filenames,
    title_column_configured,
)

CANDIDATES = frozenset({"report.csv", "report (1).csv", "notes.md", "old.md"})


def test_exact_match_wins_over_suffix_stripping():
    """A single-row 'report (1).csv' must not be mangled into 'report'.
    Rule 2 is checked before rule 3 precisely for this case (spec 6.3)."""
    assert recover_filename("report (1).csv", CANDIDATES) == "report (1).csv"


def test_multi_row_structured_title_strips_the_row_suffix():
    assert recover_filename("report.csv (0)", CANDIDATES) == "report.csv"
    assert recover_filename("report.csv (12)", CANDIDATES) == "report.csv"


def test_plain_text_title_is_the_filename():
    assert recover_filename("notes.md", CANDIDATES) == "notes.md"


def test_unmatched_title_maps_to_nothing():
    assert recover_filename("Q3 revenue", CANDIDATES) is None
    assert recover_filename("absent.csv (0)", CANDIDATES) is None


def test_recover_filenames_drops_unmatched_and_dedupes():
    titles = ["notes.md", "report.csv (0)", "report.csv (1)", "Q3 revenue"]
    assert recover_filenames(titles, CANDIDATES) == frozenset({"notes.md", "report.csv"})


def test_candidates_may_include_names_no_longer_on_disk():
    """old.md was deleted from input/ but is still in the index after an
    update; a citation into it must still resolve (spec 6.3)."""
    assert recover_filename("old.md", CANDIDATES) == "old.md"


def test_title_column_configured_detects_rule_1():
    assert title_column_configured({"input": {"title_column": "name"}}) is True
    assert title_column_configured({"input": {"type": "csv"}}) is False
    assert title_column_configured({}) is False
    assert title_column_configured({"input": None}) is False
    # An empty string is not a configured column.
    assert title_column_configured({"input": {"title_column": ""}}) is False
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_domain_artifacts.py -q`
Expected: FAIL — `ImportError: cannot import name 'recover_filename'`.

- [ ] **Step 7: Extend `domain/artifacts.py`**

Append (and add `import re` plus `from collections.abc import Iterable` / `from typing import Any` to the imports):

```python
# graphrag_input/structured_file_reader.py:48-53 appends " (N)" to a
# structured file's title when the file yields more than one row. Anchored
# at the end and requiring at least one digit, so "report (1).csv" - a real
# filename - is never treated as a suffixed title.
_ROW_SUFFIX_RE = re.compile(r" \(\d+\)$")


def title_column_configured(settings_data: Any) -> bool:
    """Rule 1 of the recovery rule (spec 6.3): with input.title_column set,
    a title is arbitrary row data and no filename can be attributed to it.

    We never write title_column ourselves (adapters/workspace.py sets only
    input.type and input.file_pattern), but SettingsPanel lets a user
    hand-edit settings.yaml, so the case is reachable and is detected rather
    than assumed away.
    """
    if not isinstance(settings_data, dict):
        return False
    section = settings_data.get("input")
    if not isinstance(section, dict):
        return False
    return bool(section.get("title_column"))


def recover_filename(title: str, candidates: frozenset[str]) -> str | None:
    """Map one documents.title back to a filename, or None.

    Exact match is tried FIRST so a single-row file genuinely named
    "report (1).csv" is not stripped down to a name that does not exist.
    """
    if title in candidates:
        return title
    stripped = _ROW_SUFFIX_RE.sub("", title)
    if stripped != title and stripped in candidates:
        return stripped
    return None


def recover_filenames(titles: Iterable[str], candidates: frozenset[str]) -> frozenset[str]:
    """Filenames attributable to a set of titles; unmatched titles vanish."""
    out = {recover_filename(t, candidates) for t in titles}
    return frozenset(n for n in out if n is not None)
```

- [ ] **Step 8: Run the domain tests**

Run: `cd backend && uv run pytest tests/test_domain_artifacts.py tests/test_domain_files.py -q`
Expected: PASS.

- [ ] **Step 9: Full gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src/graphrag_ui/domain backend/tests/test_domain_files.py backend/tests/test_domain_artifacts.py
git commit -m "$(cat <<'EOF'
feat(kb): add pure index-state and title-recovery rules

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 3: The project-row lock and the input freeze

**Files:**
- Create: `backend/src/graphrag_ui/services/project_lock.py`
- Modify: `backend/src/graphrag_ui/services/errors.py`, `services/files.py`, `services/settings.py`, `services/env_file.py`, `services/jobs.py`, `api/files_routes.py`, `api/settings_routes.py`, `api/env_routes.py`
- Test: `backend/tests/test_project_freeze.py` (new file)

**Interfaces:**
- Consumes: `Project` model (Task 1 untouched here).
- Produces: `services/project_lock.py` exporting `FREEZING_JOB_TYPES: tuple[str, ...] = ("index", "update")`, `async lock_project(session, project_id) -> None`, `async freezing_job(session, project_id) -> Job | None`, `async assert_input_unfrozen(session, project_id) -> None`; `services/errors.py` exporting `ProjectIndexingError` (with `.code == "project_indexing"` and `.params == {"job_type": ...}`). `services/files.save_file` and `delete_file` keep their signatures; `services/jobs.enqueue` keeps its signature and now takes the lock before inserting. Three module-level barrier seams are introduced and are part of the contract the tests bind to: `files._commit_upload`, `files._replace_into_input`, `settings._commit_settings`.

**Why a lock and not a check.** `jobs_one_active_per_project` is a partial unique index on `jobs`; it serializes job against job and nothing else. A bare "is a job active?" check loses this interleaving: an upload checks (no active job), enqueue commits, the runner captures the `start` snapshot, and only then does the upload's `os.replace` land — so the snapshot is not the indexer's input. Both sides must take the same lock, and the mutation side must **re-check inside it**.

- [ ] **Step 1: Write the failing freeze tests, barriers included**

Create `backend/tests/test_project_freeze.py`:

```python
"""Input freeze (spec 5.2b): upload/delete/settings/.env are refused while
an index or update job holds the project, and the lock - not the check -
is what makes the start snapshot the indexer's fixed input.

The barrier tests matter more than the 409 tests. A test that only asserts
"active job -> 409" passes against the broken check-then-act design; only
an interleaving distinguishes them, so two of them are written by hand with
explicit park points.
"""

import asyncio
import uuid

import pytest

from graphrag_ui.adapters.db import make_engine, make_session_factory
from graphrag_ui.adapters.models import Job, Project
from graphrag_ui.services import files as files_service
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services.errors import ProjectIndexingError
from graphrag_ui.services.projects import ws_path
from tests.test_files import _alice, _make_project, _upload


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
                    s1, await s1.get(Project, pid_u), "late.md",
                    _Bytes(b"late"), actor_id=owner_id,
                )
            )
            await asyncio.wait_for(parked.wait(), timeout=5)
            await jobs_service.enqueue(
                s2, await s2.get(Project, pid_u), "index", "standard", owner
            )
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
                    s1, await s1.get(Project, pid_u), "early.md",
                    _Bytes(b"early"), actor_id=owner_id,
                )
            )
            await asyncio.wait_for(holding.wait(), timeout=5)

            enqueuer = asyncio.create_task(
                jobs_service.enqueue(
                    s2, await s2.get(Project, pid_u), "index", "standard", owner
                )
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
                    s1, await s1.get(Project, pid_u),
                    content + "\n# edited\n", expected, owner_id,
                )
            )
            await asyncio.wait_for(parked.wait(), timeout=5)
            await jobs_service.enqueue(
                s2, await s2.get(Project, pid_u), "index", "standard", owner
            )
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
            f"/api/projects/{pid}/settings", headers=alice,
            json={"content": patched, "expected_hash": h},
        )
    ).status_code == 200

    # settings.yaml unchanged from here on; only .env moves.
    assert title_column_configured(_effective_settings(project)) is True
    assert (
        await client.delete(f"/api/projects/{pid}/env/TITLE_COLUMN", headers=alice)
    ).status_code in (204, 400)
```

> `monkeypatch_attr(module, name, value)` is a two-line helper at the top of
> this module (`setattr(module, name, value)`); it exists so the intent —
> "install a barrier seam" — reads at the call site. `_effective_settings`
> renders `settings.yaml` through the same `string.Template` + `.env` overlay
> that `services/settings.py:84-90` already uses, so the assertion mirrors
> graphrag's own substitution order rather than a second guess at it.

> Note for the implementer: the barrier tests need seams. Extract
> `_commit_upload(session, project, name, size, sha, actor_id, tmp, target)`
> and `_replace_into_input(tmp, target)` as module-level functions in
> `services/files.py` (Step 4), and `_commit_settings(session)` in
> `services/settings.py` (Step 6). `save_file` calls `_commit_upload` by its
> plain module-level name, which resolves through module globals at call
> time — that is what makes the `setattr` above take effect, the same seam
> `runner_loop` already uses for `IndexRunner`. A local alias or a
> `from … import _commit_upload` would defeat it.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_project_freeze.py -q`
Expected: FAIL — `ImportError: cannot import name 'ProjectIndexingError'`.

- [ ] **Step 3: Write `services/project_lock.py`**

```python
"""Project-row lock and the input freeze (spec 5.2b).

jobs_one_active_per_project serializes job against job; it cannot serialize
job enqueue against filesystem mutation. Both sides therefore take the SAME
project-scoped lock, and the mutating side re-checks for an active job
INSIDE it - the re-check is what closes the check-then-act race, not the
check itself.

The freeze covers index and update only. A test_run job reads output/ and
never input/, so freezing document work for it would be ceremony.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Job, Project
from graphrag_ui.services.errors import ProjectIndexingError

FREEZING_JOB_TYPES: tuple[str, ...] = ("index", "update")
_ACTIVE = ("queued", "running")


async def lock_project(session: AsyncSession, project_id: uuid.UUID) -> None:
    """SELECT ... FOR UPDATE on the project row. Held until the caller's
    transaction ends, which is why callers stream uploads OUTSIDE it."""
    await session.execute(
        select(Project.id).where(Project.id == project_id).with_for_update()
    )


async def freezing_job(session: AsyncSession, project_id: uuid.UUID) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(
                Job.project_id == project_id,
                Job.status.in_(_ACTIVE),
                Job.type.in_(FREEZING_JOB_TYPES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def assert_input_unfrozen(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Raise ProjectIndexingError when an index/update job holds the project.
    Call this AFTER lock_project within the committing transaction."""
    job = await freezing_job(session, project_id)
    if job is not None:
        raise ProjectIndexingError(str(job.id), job.type)
```

Add to `services/errors.py`:

```python
class ProjectIndexingError(RuntimeError):
    """An index/update job holds the project; input and configuration are
    frozen for its duration (spec 5.2b). Routes map to 409."""

    def __init__(self, job_id: str, job_type: str) -> None:
        super().__init__(f"project is being indexed by job {job_id}")
        self.code = "project_indexing"
        self.params = {"job_type": job_type}
```

- [ ] **Step 4: Restructure `services/files.py::save_file` and `delete_file`**

Replace the transaction half of `save_file` (keep the streaming loop and the
quota check exactly as they are) and add the two seams:

```python
def _replace_into_input(tmp: Path, target: Path) -> None:
    """Atomic rename; a seam so tests can park inside the locked transaction."""
    os.replace(tmp, target)


async def _commit_upload(
    session: AsyncSession,
    project: Project,
    name: str,
    size: int,
    sha: str,
    actor_id: uuid.UUID | None,
    tmp: Path,
    target: Path,
) -> None:
    """The committing transaction: lock, re-check the freeze, write the audit
    row and project_files, rename, commit.

    The lock is taken HERE and not around the upload stream: holding it for
    the whole of a multi-gigabyte upload would block job enqueue for that
    long. Taking it only for the rename keeps the window short and still
    serializes, because enqueue takes the same lock (spec 5.2b).
    """
    await lock_project(session, project.id)
    await assert_input_unfrozen(session, project.id)
    await audit(
        session, actor_id, "file.uploaded", "project", str(project.id),
        {"name": name, "size": size},
    )
    await _upsert_project_file(
        session, project.id, name=name, sha256=sha, size=size, actor_id=actor_id
    )
    await session.flush()
    _replace_into_input(tmp, target)
    await session.commit()
```

`save_file` computes the sha256 in the same streaming loop that already
passes the bytes (`h = hashlib.sha256()`, `h.update(chunk)` next to
`out.write(chunk)` — the bytes are already in hand, so hashing costs no
extra disk read), then calls `_commit_upload(...)` inside the existing
`try/except Exception: rollback; raise` / `finally: tmp.unlink(missing_ok=True)`
frame. `_upsert_project_file` inserts or updates the `project_files` row,
setting `uploaded_by=actor_id`, `uploaded_at=now()`, `discovered_at=None`.

`delete_file` gains the same shape: `lock_project` → `assert_input_unfrozen`
→ audit → delete the `project_files` row (its `file_tag_links` cascade) →
`flush` → `unlink` → `commit`.

- [ ] **Step 5: Take the lock in `services/jobs.py::enqueue`**

Immediately before `jobs_repo.insert_job`, inside the existing `try`:

```python
        # Same lock the file/settings/.env mutations take (spec 5.2b): a
        # mutation already holding it makes this wait until its rename has
        # committed, so the start snapshot cannot miss it.
        await lock_project(session, project.id)
```

The `IntegrityError → JobConflictError` mapping is unchanged: the partial
unique index still owns job-against-job exclusion.

- [ ] **Step 6: Freeze configuration writes**

`services/settings.py::write_settings` — after the conflict check and
validation, before writing bytes to disk:

```python
    await lock_project(session, project.id)
    await assert_input_unfrozen(session, project.id)
```

and its final `await session.commit()` moves into a module-level
`async def _commit_settings(session) -> None`, the barrier seam the
config-freeze test parks in.

`services/env_file.py::set_env_key` and `delete_env_key` — the same pair as
the first statements of their `try` block, before the audit row.

The `.env` freeze is not belt-and-braces: graphrag runs strict
`string.Template` substitution over `settings.yaml` against `os.environ`
overlaid by the workspace `.env` **before** parsing it, so
`input.title_column: ${TITLE_COLUMN}` moves the effective configuration
through `.env` alone. `settings.py:84-90` already mirrors that order.

- [ ] **Step 7: Map the error in the three route modules**

In `api/files_routes.py` (upload + delete), `api/settings_routes.py`
(`put_settings`) and `api/env_routes.py` (`patch_env` + `delete_env`), add
to each `try`:

```python
        except ProjectIndexingError as e:
            raise ApiError(
                status.HTTP_409_CONFLICT, e.code, str(e), e.params
            ) from None
```

- [ ] **Step 8: Run the freeze tests**

Run: `cd backend && uv run pytest tests/test_project_freeze.py -q`
Expected: PASS (7 tests). If the two blocking barriers pass but
`test_enqueue_waits_for_an_upload_holding_the_lock`'s
`assert done == set()` fails, `enqueue` is not taking the lock — the
`IntegrityError` path alone cannot produce that wait.

- [ ] **Step 9: Regenerate the contract, run the full gate, commit**

```bash
cd backend && uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src backend/tests/test_project_freeze.py openapi.json frontend/src/api/types.generated.ts
git commit -m "$(cat <<'EOF'
feat(kb): freeze input and configuration behind a project-row lock

Uploads, deletes, settings writes and .env edits now take the same
project-scoped lock that job enqueue takes, and re-check for an active
index/update job inside it. A bare check-then-act let an upload land
after the start snapshot was captured.

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 4: Snapshot capture, artifact epoch, baseline advancement

**Files:**
- Create: `backend/src/graphrag_ui/services/index_snapshots.py`
- Modify: `backend/src/graphrag_ui/adapters/artifacts.py`, `adapters/jobs_repo.py`, `services/runner_loop.py`, `services/retention.py`, `services/files.py`
- Test: `backend/tests/test_index_snapshots.py` (new), `backend/tests/test_retention.py` (extend), `backend/tests/test_real_corpus_titles.py` (new, `@pytest.mark.slow`)

**Interfaces:**
- Consumes: `IndexSnapshot`, `IndexSnapshotEntry`, `Project.baseline_snapshot_id`, `Project.artifact_epoch` (Task 1); `recover_filenames`, `title_column_configured` (Task 2).
- Produces:
  - `adapters/artifacts.py`: `read_document_titles(root: Path) -> list[str] | None` — `None` when `output/documents.parquet` is absent.
  - `services/index_snapshots.py`:
    - `async capture_start(session, project_id, job_id) -> uuid.UUID`
    - `async bump_artifact_epoch(session, project_id) -> int` — returns the new value
    - `async promote(session, job) -> None` — call inside the finishing transaction; a no-op unless the job succeeded and its type qualifies
    - `async promote_after_finish(session, job_id, status) -> None` — the `on_before_commit` callback shape
    - `async baseline_entries(session, project_id) -> dict[str, str]`
    - `async baseline_row(session, project_id) -> IndexSnapshot | None`
    - `async entries_of(session, snapshot_id) -> dict[str, str]`
    - `async kinds_of(session, job_id) -> set[str]`
    - `def advance_entries(previous, start, pre) -> dict[str, str]` — pure, the §5.2(c) rule
  - `adapters/jobs_repo.finish(..., on_before_commit: Callable[[AsyncSession], Awaitable[None]] | None = None)`.
  - `services/files.py`: `sha256_file(path: Path) -> str`.

**The advancement rule is a mirror of upstream, not a hedge.** GraphRAG 3.1.0's `get_delta_docs` (`graphrag/index/update/incremental_index.py:46`) compares `documents.title` only, and `load_update_documents.py:62` returns `new_inputs` alone — `deleted_inputs` is computed and discarded. So after an `update`, a same-name file whose content changed was *not* re-ingested, and a deleted file is still fully in the index. Replacing the baseline with the current `input/` would erase exactly the `modified` and `removed` states that were correct.

- [ ] **Step 1: Write the failing snapshot tests**

Create `backend/tests/test_index_snapshots.py`. The parametrized case list is the §5.2(c) mirror table verbatim:

```python
"""Index snapshots (spec 5.2): what the indexer was handed, what it
produced, and how the baseline advances.

The seven-row mirror table is the point of this module. Two earlier rules
failed rows three and four: a filename-only condition pinned a repaired
document at `modified` forever, and adding "N in post" to the condition did
the same to a document that was retried and dropped again.
"""

import uuid

import pytest

from graphrag_ui.adapters.models import IndexSnapshot, IndexSnapshotEntry, Job, Project
from graphrag_ui.services import index_snapshots


async def _project(db_session, tmp_path) -> Project: ...  # helper: see below
async def _job(db_session, project, type_="index", status="running") -> Job: ...


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


async def test_update_with_no_previous_baseline_writes_no_baseline(
    db_session, project_with_files
):
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
            {"a.md": "h1"}, {"a.md": "h2"}, {"a.md"}, {"a.md"}, {"a.md": "h1"},
        ),
        (
            "deleted -> still in the index, entry kept",
            {"a.md": "h1"}, {}, {"a.md"}, {"a.md"}, {"a.md": "h1"},
        ),
        (
            "previously skipped, now fixed -> ingested, advances",
            {"a.md": "h1"}, {"a.md": "h2"}, set(), {"a.md"}, {"a.md": "h2"},
        ),
        (
            "previously skipped, edited, dropped again -> advances anyway",
            {"a.md": "h1"}, {"a.md": "h2"}, set(), set(), {"a.md": "h2"},
        ),
        (
            "previously skipped, untouched -> advances (no-op)",
            {"a.md": "h1"}, {"a.md": "h1"}, set(), set(), {"a.md": "h1"},
        ),
        (
            "brand new, ingested -> added",
            {}, {"n.md": "h9"}, set(), {"n.md"}, {"n.md": "h9"},
        ),
        (
            "brand new, silently dropped -> added",
            {}, {"n.md": "h9"}, set(), set(), {"n.md": "h9"},
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

    job = await jobs_service.enqueue(
        db_session, project_with_files[0], "index", "standard", ...
    )
    await db_session.refresh(project_with_files[0])
    assert project_with_files[0].artifact_epoch == 0
```

Write the three fixtures (`project_with_files`, `project_with_baseline`, `_job`) at the top of the module using `FakeInitializer`-style workspaces under `tmp_path`, following `tests/test_files.py::project`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_index_snapshots.py -q`
Expected: FAIL — `ModuleNotFoundError: graphrag_ui.services.index_snapshots`.

- [ ] **Step 3: Add the adapter read and the file hash**

`adapters/artifacts.py`:

```python
def read_document_titles(root: Path) -> list[str] | None:
    """documents.title for every indexed document, or None when the parquet
    is absent. One duckdb read of a single column - the whole documents table
    is never loaded (documents is not in FrameCache.TABLES)."""
    path = root / "output" / "documents.parquet"
    if not path.is_file():
        return None
    with duckdb.connect(":memory:") as con:
        rows = con.execute("SELECT title FROM read_parquet(?)", [str(path)]).fetchall()
    return [str(r[0]) for r in rows if r[0] is not None]
```

`services/files.py`:

```python
def sha256_file(path: Path) -> str:
    """Streaming sha256; used by discovery and by the start snapshot, which
    both hash files nobody just uploaded."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 4: Write `services/index_snapshots.py`**

The module's shape (write it in full; the docstrings below are the ones the
file must carry, because each records a decision review forced):

```python
"""Index snapshots: the evidence per-file state is computed from (spec 5.2).

Three parts, and each exists because a simpler version lied:

(a) The start snapshot is captured from input/ BEFORE the CLI spawns. A
    post-hoc scan records the hash of whatever is on disk when the job ends,
    which is not what the indexer read.
(b) The input freeze (services/project_lock.py) keeps input/ still for the
    job's duration, so (a) is actually fixed.
(c) Baseline advancement is type-specific and driven by attributable
    document titles, because graphrag compares documents.title and an
    update does not re-ingest a changed same-name file at all.
"""


def advance_entries(
    previous: Mapping[str, str], start: Mapping[str, str], pre: AbstractSet[str]
) -> dict[str, str]:
    """The per-name rule for a successful update with recovery available.

    `pre` is the START row's attributable set: it says whether upstream
    already knew this title and therefore skipped it. It is NOT paired with
    a `post` condition - requiring the attempt to have succeeded as well
    would strand a document that was offered and dropped AGAIN (content
    repaired, title still unknown, dropped once more), keeping the stale
    hash so the UI said `modified` when the truth was `skipped`.
    """
    out = dict(previous)  # names absent from `start` are kept: they are the
                          # `removed` files, still live in the index
    for name, sha in start.items():
        if name not in previous or name not in pre:
            out[name] = sha
    return out
```

`capture_start` computes, in one transaction of its own:
`entries` = `{name: sha256_file(p)}` over `input/`; `candidates` = the
previous baseline's entry names ∪ `entries` keys — **not** a live `input/`
listing, because a live listing drops exactly the names whose files are gone
and those are the documents that stay in the index after a delete;
`titles = read_document_titles(root)`; `title_recovery` =
`"unavailable_title_column"` when `title_column_configured(yaml.safe_load(settings.yaml))`
else `"available"`; `attributable_titles` = `sorted(recover_filenames(titles or [], candidates))`
when available else `[]`.

`promote(session, job)` runs inside the caller's transaction and is a no-op
unless `job.status == "succeeded"`. For `index`: copy the start row's
entries wholesale. For `update`: return early when there is no previous
baseline (writing nothing at all); otherwise write a new `baseline` row —
always, even when `title_recovery` is unavailable at either capture, in
which case the entries carry forward verbatim. Then set
`projects.baseline_snapshot_id` and stamp `artifact_epoch` from
`projects.artifact_epoch` onto the new row. All of it in the caller's
transaction, so a crash cannot leave a project pointing at a baseline for a
job that never finished.

`bump_artifact_epoch` runs in its own transaction:
`UPDATE projects SET artifact_epoch = artifact_epoch + 1 WHERE id = :id RETURNING artifact_epoch`.

- [ ] **Step 5: Wire the runner**

`adapters/jobs_repo.finish` gains the callback:

```python
async def finish(
    session, job_id, status, *, exit_code=None, error=None, stats=None,
    on_before_commit=None,
):
    if status not in TERMINAL_STATUSES:      # unchanged
        raise ValueError(f"non-terminal finish status: {status}")
    await session.execute(update(Job).where(Job.id == job_id).values(...))
    if on_before_commit is not None:
        # Baseline promotion must land in the SAME transaction that marks the
        # job succeeded (spec 5.2): otherwise a crash between the two leaves
        # a project pointing at a baseline for a job that never finished.
        await on_before_commit(session)
    await session.commit()
```

`services/runner_loop.py::_execute` — before `IndexRunner().run(...)`:

```python
    if job_type in FREEZING_JOB_TYPES:
        async with get_session_factory()() as s:
            await index_snapshots.capture_start(s, project_id, job_id)
        async with get_session_factory()() as s:
            # Immediately before the spawn, promoted or not: every attempt
            # that can write output/ moves the epoch (spec 7.4).
            await index_snapshots.bump_artifact_epoch(s, project_id)
```

and at the terminal write:

```python
    async with get_session_factory()() as s:
        job = await jobs_repo.get_job(s, job_id)
        await jobs_repo.finish(
            s, job_id, res.status, exit_code=res.exit_code, error=res.error,
            stats=res.stats,
            on_before_commit=(
                None if job is None
                else lambda sess: index_snapshots.promote_after_finish(sess, job_id, res.status)
            ),
        )
```

`promote_after_finish(session, job_id, status)` re-reads the job row inside
the transaction and delegates to `promote`, so the callback carries no stale
instance across sessions.

- [ ] **Step 6: Prune superseded start snapshots in the retention sweep**

This is a **new responsibility** for the sweep and changes its contract.
`services/retention.py:1-3` currently states "DB rows are never deleted —
history and the error tail in `jobs.error` survive; only files are
reclaimed." That invariant protects job *history*, and a snapshot of
superseded input hashes makes no historical claim — but the docstring must
change with the behavior. Add:

```python
async def sweep_index_snapshots(session, now: datetime) -> dict:
    """Delete `start` snapshot rows of terminal jobs past the same retention
    window their logs use. NEVER touches the row referenced by
    projects.baseline_snapshot_id, nor the start row of the job that produced
    it - those are the current evidence, not superseded input hashes.
    `baseline` rows are never pruned here."""
```

and call it from `sweep_all`. Extend `tests/test_retention.py` with a case
proving the current baseline's job keeps **both** its rows while an older
failed job's `start` row is gone.

- [ ] **Step 7: Write the slow upstream-pinning test**

Create `backend/tests/test_real_corpus_titles.py`, marked `@pytest.mark.slow`,
following `tests/test_real_corpus_jobs.py`'s fixtures. It forks the real
graphrag CLI over four tiny workspaces — `text`, multi-row CSV, multi-row
JSON, and a `title_column` project — and asserts that
`recover_filenames(read_document_titles(root), candidates)` equals the
uploaded filenames in the first three, and that `title_column_configured`
fires on the fourth. §6.3's rule is read off upstream source; it must be
pinned against upstream behavior, not trusted.

- [ ] **Step 8: Run the tests**

Run: `cd backend && uv run pytest tests/test_index_snapshots.py tests/test_retention.py tests/test_runner_loop.py -q`
Expected: PASS.

- [ ] **Step 9: Full gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src backend/tests
git commit -m "$(cat <<'EOF'
feat(kb): capture index snapshots and advance the baseline on success

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 5: File listing — union enumeration, discovery, ingest_check

**Files:**
- Modify: `backend/src/graphrag_ui/services/files.py`, `backend/src/graphrag_ui/api/files_routes.py`
- Test: `backend/tests/test_files.py` (extend)

**Interfaces:**
- Consumes: `index_state`, `AttributableTitles`, `IngestCheck` (Task 2); `baseline_entries`, `baseline_row` (Task 4); `sha256_file` (Task 4).
- Produces: `services/files.list_files(session, project) -> dict` returning
  `{"files": [...], "ingest_check": str, "has_baseline": bool}`; each file entry is
  `{name, size: int | None, modified_at: str | None, sha256: str | None, index_state: str, tags: list[str]}`.
  `FileEntryOut` widens `size` and `modified_at` to `| None` and gains
  `sha256: str | None`, `index_state: str`, `tags: list[str]`; `FileListOut`
  gains `ingest_check: str` and `has_baseline: bool`.

**Two things the listing must NOT do.** It must not read
`documents.parquet` — recovery provenance is stored on the baseline row, and
reading the parquet at listing time would bind the answer to *today's*
configuration while the titles were written under yesterday's. And it must
not enumerate `input/` alone: after a normal delete the filesystem has
nothing and `project_files` has nothing, so an FS-driven listing could never
produce a `removed` row, and that state, the health count and overview card
4 would all be unreachable. The enumeration set is `input/` ∪ the baseline's
filenames.

- [ ] **Step 1: Write the failing listing tests**

Append to `backend/tests/test_files.py`:

```python
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
    assert (await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)).status_code == 204

    body = await _list(client, alice, pid)
    row = next(f for f in body["files"] if f["name"] == "a.md")
    assert row["index_state"] == "removed"
    assert row["size"] is None and row["modified_at"] is None and row["sha256"] is None
    assert row["tags"] == []
    assert len(body["files"]) == 2


async def test_no_file_operation_applies_to_a_removed_row(client, indexed_project):
    alice, pid = indexed_project
    await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)

    assert (await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)).status_code == 404
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
        await db_session.execute(
            select(ProjectFile).where(ProjectFile.project_id == uuid.UUID(pid))
        )
    ).scalars().all()
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_files.py -q -k "listing or removed or discovered or ingest_check or title_column or skipped or modified_and_indexed"`
Expected: FAIL — `KeyError: 'ingest_check'`.

- [ ] **Step 3: Rewrite `list_files`**

```python
async def list_files(session: AsyncSession, project: Project) -> dict:
    """Rows are input/ UNION the baseline's filenames, sorted by name.

    Reads the BASELINE SNAPSHOT ROW, never documents.parquet: recovery
    provenance was evaluated when the artifacts were produced, and
    re-deriving it here would bind the answer to today's configuration
    (spec 6.3). The only artifact touch is a stat on
    output/documents.parquet, which distinguishes
    unavailable_not_indexed from available.
    """
```

Order of work: scan `input/` off the event loop (`to_thread`) for
`{name: (size, mtime, sha256)}`; load `baseline_entries` and `baseline_row`;
**discover** untracked names under `lock_project` (idempotent: insert only
names with no row); load `project_files` rows and their tags in one query
each; compute `ingest_check` — `unavailable_no_baseline` when there is no
baseline, else `unavailable_title_column` when the baseline row says so, else
`unavailable_not_indexed` when `output/documents.parquet` does not `stat`,
else `available`; build `AttributableTitles.of(baseline_row.attributable_titles)`
when available else `.unavailable()`; emit one row per name in the union
with `index_state(...)`, nulling `size`/`modified_at`/`sha256`/`tags` for
names absent from `input/`.

- [ ] **Step 4: Widen the API models**

In `api/files_routes.py`:

```python
class FileEntryOut(BaseModel):
    name: str
    # Nullable because a `removed` row has no file behind it (spec 6.1).
    # Inventing a zero size or the deletion timestamp would let the UI sort
    # and total them as if they were files.
    size: int | None
    modified_at: str | None
    sha256: str | None
    index_state: str
    tags: list[str] = []


class FileListOut(BaseModel):
    files: list[FileEntryOut]
    usage_bytes: int
    quota_bytes: int
    # Whether `skipped` can be emitted at all, and why not. On the response,
    # not on each row: it is a property of the artifacts, and repeating it
    # per file would invite the UI to render it per file (spec 6.3).
    ingest_check: str
    has_baseline: bool
```

`list_files` route passes `db` through. `delete_file` and the preview route
404 for a name that is not on disk — no special case needed beyond the
existing `target.is_file()` check, which already produces
`FileNotFoundError` → 404.

- [ ] **Step 5: Run the file tests**

Run: `cd backend && uv run pytest tests/test_files.py -q`
Expected: PASS.

- [ ] **Step 6: Regenerate the contract, full gate, commit**

```bash
cd backend && uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
cd ../frontend && npx tsc -b --noEmit
```

The frontend will not compile yet: `FilesPanel.tsx` reads `f.size` and
`f.modified_at` as non-null. Fix it minimally here — `humanBytes(f.size ?? 0)`
guarded by a `f.size === null ? "—"` branch — and leave the real rendering to
Task 8. A compile error here is the generated types doing their job.

```bash
git add backend frontend/src openapi.json
git commit -m "$(cat <<'EOF'
feat(kb): list files with index state, tags and an ingest check

BREAKING: FileEntryOut.size and .modified_at are nullable and listings can
contain names with no file behind them - a `removed` document is still in
the index and only a full rebuild clears it.

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 6: Tags and bulk delete

**Files:**
- Modify: `backend/src/graphrag_ui/services/files.py`, `backend/src/graphrag_ui/api/files_routes.py`
- Test: `backend/tests/test_files.py` (extend), `backend/tests/test_project_freeze.py` (extend)

**Interfaces:**
- Consumes: `lock_project`, `assert_input_unfrozen` (Task 3); `ProjectFile`, `FileTag`, `FileTagLink` (Task 1).
- Produces: `services/files.py`: `async add_tags(session, project, names, tags, actor_id)`,
  `async remove_tags(session, project, names, tags, actor_id)`,
  `async list_tags(session, project) -> list[dict]` (`{name, count}`),
  `async bulk_delete(session, project, names, actor_id) -> dict` (`{deleted, bytes}`).
  Routes: `POST/DELETE /api/projects/{pid}/files/{name}/tags` (`project:edit_content`),
  `GET /api/projects/{pid}/tags` (`project:view`),
  `POST /api/projects/{pid}/files:bulk-delete` (`project:edit_content`).

**Tags are metadata, not input, so they are NOT frozen.** Tagging a document
while an index runs changes nothing the indexer reads. Bulk delete *is*
input and takes the same lock and the same 409 as a single delete.

- [ ] **Step 1: Write the failing tests**

```python
async def test_tags_round_trip_and_catalog_counts(client, indexed_project):
    alice, pid = indexed_project
    r = await client.post(
        f"/api/projects/{pid}/files/a.md/tags", headers=alice, json={"tags": ["policy", "q3"]}
    )
    assert r.status_code == 204
    await client.post(
        f"/api/projects/{pid}/files/b.md/tags", headers=alice, json={"tags": ["policy"]}
    )

    body = await _list(client, alice, pid)
    assert sorted(next(f for f in body["files"] if f["name"] == "a.md")["tags"]) == ["policy", "q3"]

    catalog = (await client.get(f"/api/projects/{pid}/tags", headers=alice)).json()
    assert {t["name"]: t["count"] for t in catalog["tags"]} == {"policy": 2, "q3": 1}

    r = await client.request(
        "DELETE", f"/api/projects/{pid}/files/a.md/tags", headers=alice, json={"tags": ["q3"]}
    )
    assert r.status_code == 204
    body = await _list(client, alice, pid)
    assert next(f for f in body["files"] if f["name"] == "a.md")["tags"] == ["policy"]


async def test_tagging_is_allowed_while_indexing(client, db_session, indexed_project):
    """Tags are metadata, not input (spec 8)."""
    alice, pid = indexed_project
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id)

    r = await client.post(
        f"/api/projects/{pid}/files/a.md/tags", headers=alice, json={"tags": ["x"]}
    )
    assert r.status_code == 204


async def test_tagging_a_removed_row_is_404(client, indexed_project):
    alice, pid = indexed_project
    await client.delete(f"/api/projects/{pid}/files/a.md", headers=alice)
    r = await client.post(
        f"/api/projects/{pid}/files/a.md/tags", headers=alice, json={"tags": ["x"]}
    )
    assert r.status_code == 404


async def test_bulk_delete_removes_files_rows_and_audits_each(client, db_session, indexed_project):
    alice, pid = indexed_project
    r = await client.post(
        f"/api/projects/{pid}/files:bulk-delete", headers=alice, json={"names": ["a.md", "b.md"]}
    )
    assert r.status_code == 200 and r.json()["deleted"] == 2

    actions = (
        await db_session.execute(
            select(AuditLog.action).where(AuditLog.target_id == pid, AuditLog.action == "file.deleted")
        )
    ).scalars().all()
    assert len(actions) == 2


async def test_bulk_delete_is_refused_while_indexing(client, db_session, indexed_project):
    alice, pid = indexed_project
    project = await db_session.get(Project, uuid.UUID(pid))
    await _queue_index_job(db_session, project.id, project.owner_id)

    r = await client.post(
        f"/api/projects/{pid}/files:bulk-delete", headers=alice, json={"names": ["a.md"]}
    )
    assert r.status_code == 409 and r.json()["code"] == "project_indexing"


async def test_bulk_delete_is_all_or_nothing_on_an_unknown_name(client, indexed_project):
    alice, pid = indexed_project
    r = await client.post(
        f"/api/projects/{pid}/files:bulk-delete", headers=alice, json={"names": ["a.md", "ghost.md"]}
    )
    assert r.status_code == 404
    body = await _list(client, alice, pid)
    assert {f["name"] for f in body["files"]} == {"a.md", "b.md"}


@pytest.mark.parametrize(
    ("method", "path", "body", "viewer_status"),
    [
        ("GET", "/api/projects/{pid}/tags", None, 200),
        ("GET", "/api/projects/{pid}/files/a.md/preview", None, 200),
        ("POST", "/api/projects/{pid}/files/a.md/preview", {"passage": "x"}, 200),
        ("POST", "/api/projects/{pid}/files/a.md/tags", {"tags": ["x"]}, 403),
        ("DELETE", "/api/projects/{pid}/files/a.md/tags", {"tags": ["x"]}, 403),
        ("POST", "/api/projects/{pid}/files:bulk-delete", {"names": ["a.md"]}, 403),
    ],
)
async def test_route_authz_for_every_new_endpoint(
    client, indexed_project_with_viewer, method, path, body, viewer_status
):
    """Spec 8: reading is project:view, curating content is
    project:edit_content. A viewer may read the tag catalog and preview a
    document and may not tag, untag or bulk-delete."""
    viewer, pid = indexed_project_with_viewer
    r = await client.request(method, path.format(pid=pid), headers=viewer, json=body)
    assert r.status_code == viewer_status
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_files.py -q -k "tags or bulk"`
Expected: FAIL — 404 / 405 on the new routes.

- [ ] **Step 3: Implement the service functions**

`add_tags` upserts `file_tags` rows per name under the project (unique
`(project_id, name)`) and inserts missing `file_tag_links`, audits
`file.tagged` with `{name, tags}`, commits. `remove_tags` deletes links and
audits `file.untagged`. Neither takes the freeze — but both take
`lock_project`, because they write `project_files`-adjacent rows and
discovery may run concurrently. `list_tags` is one grouped query.
`bulk_delete` validates every name resolves to a real file **before** any
unlink (all-or-nothing), then runs the single-delete body per name inside
one locked transaction, so a partial bulk delete cannot leave audit rows for
files that still exist.

- [ ] **Step 4: Add the routes**

Bodies are pydantic models with `extra="forbid"`; `TagsIn.tags` is
`list[str]` with `min_length=1`, each tag `min_length=1, max_length=50`;
`BulkDeleteIn.names` is `list[str]` with `min_length=1, max_length=500`.
Atoms: `project:edit_content` for the three mutating routes,
`project:view` for `GET /tags`.

- [ ] **Step 5-7: Test, regenerate, commit**

```bash
cd backend && uv run pytest tests/test_files.py tests/test_project_freeze.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(kb): add file tags and bulk delete

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 7: Document preview

**Files:**
- Modify: `backend/src/graphrag_ui/services/files.py`, `backend/src/graphrag_ui/api/files_routes.py`
- Test: `backend/tests/test_files.py` (extend)

**Interfaces:**
- Consumes: `_safe_name`, `ws_path`.
- Produces: `services/files.preview_file(project, name, *, around: str | None = None) -> dict`
  returning `{"text": str, "offset": int, "total_size": int, "match": bool}`;
  constants `PREVIEW_WINDOW_BYTES = 64 * 1024` and `PASSAGE_MAX_BYTES = 4096`.
  Routes `GET /api/projects/{pid}/files/{name}/preview` (head window) and
  `POST` of the same path with body `{"passage": str}`.

**The cap is on the returned window, not on how much of the file may be
scanned.** With `around`, the file is streamed in bounded chunks to locate
the first occurrence *anywhere* in it, and the window is centered there.
Those two claims only conflicted while the cap was described as a read
limit. With no `around` the window is the head; with `around` unmatched, the
head plus `match: false` — rather than pretending.

- [ ] **Step 1: Write the failing preview tests**

```python
async def test_preview_returns_the_head_window(client, indexed_project):
    alice, pid = indexed_project
    r = await client.get(f"/api/projects/{pid}/files/a.md/preview", headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 0 and body["match"] is False
    assert len(body["text"].encode()) <= 64 * 1024


async def test_preview_finds_a_match_beyond_the_first_window(client, alice_project):
    """The 64 KiB cap bounds the RESPONSE, not the scan."""
    alice, pid = alice_project
    needle = "NEEDLE-8f3c"
    await _upload(client, alice, pid, "big.md", b"x" * 200_000 + needle.encode() + b"y" * 5_000)

    r = await client.post(
        f"/api/projects/{pid}/files/big.md/preview", headers=alice, json={"passage": needle}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["match"] is True
    assert needle in body["text"]
    assert body["offset"] > 64 * 1024


async def test_unmatched_passage_returns_the_head_and_says_so(client, alice_project):
    alice, pid = alice_project
    await _upload(client, alice, pid, "a.md", b"hello world")
    r = await client.post(
        f"/api/projects/{pid}/files/a.md/preview", headers=alice, json={"passage": "absent"}
    )
    assert r.status_code == 200 and r.json()["match"] is False
    assert r.json()["offset"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {},                                        # neither form
        {"passage": ""},                           # empty passage
        {"passage": "x", "result_id": "abc"},      # mixed / unknown field
        {"result_id": "abc"},                      # slice 3's form, not yet served
        {"passage": "x", "unknown": 1},            # extra="forbid"
    ],
)
async def test_locator_forms_are_exhaustive_not_permissive(client, alice_project, body):
    """A partially specified locator is a caller bug; guessing an
    interpretation is how authorization bindings get bypassed by accident."""
    alice, pid = alice_project
    await _upload(client, alice, pid, "a.md", b"hello")
    r = await client.post(
        f"/api/projects/{pid}/files/a.md/preview", headers=alice, json=body
    )
    assert r.status_code == 422


async def test_passage_bound_is_bytes_not_characters(client, alice_project):
    """pydantic's string max_length counts CHARACTERS, so a CJK passage
    would pass a character check at three times the byte budget."""
    alice, pid = alice_project
    await _upload(client, alice, pid, "a.md", b"hello")
    url = f"/api/projects/{pid}/files/a.md/preview"

    assert (await client.post(url, headers=alice, json={"passage": "a" * 4096})).status_code in (200, 404)
    assert (await client.post(url, headers=alice, json={"passage": "a" * 4097})).status_code == 422
    # 2000 CJK characters = 6000 UTF-8 bytes: under a character cap, over the byte cap.
    assert (await client.post(url, headers=alice, json={"passage": "字" * 2000})).status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_files.py -q -k preview`
Expected: FAIL — 405 on GET/POST preview.

- [ ] **Step 3: Implement `preview_file`**

Sync core run through `asyncio.to_thread`; UTF-8 decode with
`errors="replace"`. With `around`, read in `_CHUNK_BYTES` blocks carrying an
overlap of `len(needle) - 1` bytes so a match spanning a chunk boundary is
still found; on the first hit, seek back `PREVIEW_WINDOW_BYTES // 2` and
read one window.

- [ ] **Step 4: Add the routes**

```python
class PreviewIn(BaseModel):
    # extra="forbid" so a mixed or unknown-field body is a 422 rather than a
    # silently ignored key (spec 7.4).
    model_config = ConfigDict(extra="forbid")

    passage: str

    @field_validator("passage")
    @classmethod
    def _bounded_bytes(cls, v: str) -> str:
        if not v:
            raise ValueError("passage must not be empty")
        if len(v.encode("utf-8")) > PASSAGE_MAX_BYTES:
            raise ValueError(f"passage exceeds {PASSAGE_MAX_BYTES} bytes")
        return v
```

Both routes are `project:view`. A name with no file behind it (a `removed`
row) 404s.

- [ ] **Step 5-6: Test, regenerate, full gate, commit**

```bash
cd backend && uv run pytest tests/test_files.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(kb): preview a document, optionally centered on a passage

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 8: Frontend — split FilesPanel, add search, filters and index state

**Files:**
- Create: `frontend/src/components/files/FilesToolbar.tsx`, `frontend/src/components/files/FilesTable.tsx`, `frontend/src/components/files/indexState.tsx`
- Modify: `frontend/src/components/FilesPanel.tsx`, `frontend/src/api/types.ts`, `frontend/src/i18n/locales/{zh-TW,en-US}.ts`
- Test: `frontend/src/components/__tests__/FilesPanel.test.tsx` (extend, not replace)

**Interfaces:**
- Consumes: `FilesOut` / `FileEntry` from `types.generated.ts` (Tasks 5-6), the `/tags` catalog.
- Produces:
  - `indexState.tsx`: `type IndexState = "new" | "modified" | "removed" | "indexed" | "skipped"`; `STATE_COLOR: Record<IndexState, string>`; `useStateCopy()` returning `{label, sentence}` per state.
  - `FilesToolbar`: props `{ search, onSearch, tags, selectedTags, onTags, state, onState, usageBytes, quotaBytes }`.
  - `FilesTable`: props `{ files, canEdit, frozen, selected, onSelect, onDelete, onPreview }`.
  - `FilesPanel` keeps its existing props `{ projectId, inputFileType, canEdit }`.

**Filtering is client-side.** Hundreds of rows do not need server paging, and
a round trip per keystroke would be worse than the render it saves.

**The state filter reads a query param.** `?state=new,modified` is the
landing target for slice ③'s overview action cards: this slice builds the
entry point, slice ③ links to it. Read it with `useSearchParams` and write
back on change so the filter is shareable and survives a reload.

- [ ] **Step 1: Write the failing component tests**

Extend `frontend/src/components/__tests__/FilesPanel.test.tsx` — extend, do
not replace: the existing quota and `humanBytes` assertions must keep
passing through the split.

```tsx
const FILES_BODY = {
  files: [
    { name: "notes.txt", size: 1024, modified_at: "2026-08-19T00:00:00Z",
      sha256: "aa", index_state: "indexed", tags: ["policy"] },
    { name: "draft.md", size: 512, modified_at: "2026-08-19T01:00:00Z",
      sha256: "bb", index_state: "modified", tags: [] },
    { name: "gone.md", size: null, modified_at: null, sha256: null,
      index_state: "removed", tags: [] },
  ],
  usage_bytes: 1536, quota_bytes: 10240,
  ingest_check: "available", has_baseline: true,
};

test("renders an index state per row, and a removed row shows no size", async () => {
  renderPanel();
  expect(await screen.findByText("已索引")).toBeInTheDocument();
  expect(screen.getByText("已修改")).toBeInTheDocument();
  expect(screen.getByText("已刪除")).toBeInTheDocument();
  const removedRow = screen.getByText("gone.md").closest("tr")!;
  expect(within(removedRow).getByText("—")).toBeInTheDocument();
});

test("a removed row explains that only a full rebuild clears it", async () => {
  renderPanel();
  await userEvent.hover(await screen.findByText("已刪除"));
  expect(await screen.findByText(/完整重建/)).toBeInTheDocument();
});

test("filename search filters client-side", async () => {
  renderPanel();
  await userEvent.type(await screen.findByPlaceholderText("搜尋檔名…"), "draft");
  expect(screen.getByText("draft.md")).toBeInTheDocument();
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
});

test("the state filter is seeded from the ?state= query param", async () => {
  renderPanel({ route: "/projects/p1/files?state=modified" });
  expect(await screen.findByText("draft.md")).toBeInTheDocument();
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
});

test("an unavailable ingest check shows one banner naming the reason", async () => {
  renderPanel({ body: { ...FILES_BODY, ingest_check: "unavailable_title_column" } });
  expect(await screen.findByText(/靜默略過偵測已關閉/)).toBeInTheDocument();
  // One banner for the table, never one per row.
  expect(screen.getAllByText(/靜默略過偵測已關閉/)).toHaveLength(1);
});

test("no banner when the check is available", async () => {
  renderPanel();
  await screen.findByText("notes.txt");
  expect(screen.queryByText(/靜默略過偵測已關閉/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- FilesPanel`
Expected: FAIL — the state column and the toolbar do not exist.

- [ ] **Step 3: Write `indexState.tsx`**

```tsx
// Each state carries a SENTENCE, not just a colored dot: `removed` and
// `skipped` are the two the user has never seen before, and both must
// explain themselves. `removed` specifically must say that only a full
// rebuild clears it - an update leaves the document in the index (spec 9.1).
export const STATE_COLOR: Record<IndexState, string> = {
  indexed: "green", new: "blue", modified: "gold",
  removed: "red", skipped: "volcano",
};
```

`useStateCopy()` returns `{ label, sentence }` from the i18n catalog per
state, so no component builds copy from the schema value.

- [ ] **Step 4: Write `FilesToolbar` and `FilesTable`, thin `FilesPanel`**

`FilesPanel` keeps the queries, the mutations and the `Upload.Dragger`, and
becomes the composition point. The quota progress bar **moves** out of its
own block between the uploader and the table into the toolbar's right edge;
preserve `humanBytes` and the 90%-to-`exception` behavior exactly. A
persistent "N documents not yet indexed" bar (count of `new` + `modified`)
sits above the table and links to the jobs page.

- [ ] **Step 5: Add every new string to BOTH locales**

The five index states, their five explanatory sentences, the three
`ingest_check` reasons, the toolbar placeholders, and the `project_indexing`
error code. `i18n.test.ts` enforces parity — a missing en-US key is a
compile error, not a runtime fallback.

- [ ] **Step 6: Run the frontend gate**

Run: `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`
Expected: PASS. `npm run lint` is ratcheted at 6 warnings — do not raise it.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(kb): split FilesPanel and show a per-file index state

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 9: Frontend — bulk actions, frozen state, preview drawer

**Files:**
- Create: `frontend/src/components/files/FilePreviewDrawer.tsx`
- Modify: `frontend/src/components/FilesPanel.tsx`, `frontend/src/components/files/FilesTable.tsx`, `frontend/src/i18n/locales/{zh-TW,en-US}.ts`
- Test: `frontend/src/components/__tests__/FilesPanel.test.tsx` (extend)

**Interfaces:**
- Consumes: `POST /files:bulk-delete`, `POST|DELETE /files/{name}/tags`, `GET|POST /files/{name}/preview` (Tasks 6-7).
- Produces: `FilePreviewDrawer` with props
  `{ projectId: string; name: string | null; locator?: { resultId: string; entryId: number } | { passage: string }; onClose: () => void }`.

**The `locator` prop is reserved here and unused.** Slice ① never passes one
— the drawer opens from a row, which is the head window. Slice ③ passes
`{resultId, entryId}` for a stored run and `{passage}` for an ad-hoc query,
and picks the variant by where the answer came from. `entryId` is a
`number`, matching the existing `Citation.ids: number[]` in
`frontend/src/api/types.ts:40`; a string id would have needed a conversion
that exists nowhere else in this codebase.

- [ ] **Step 1: Write the failing tests**

```tsx
test("bulk delete confirms with count and total size", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /draft.md/ }));
  await userEvent.click(screen.getByRole("button", { name: "刪除所選" }));
  // Deleting 30 documents is not the same act as deleting one.
  expect(await screen.findByText(/2 個檔案/)).toBeInTheDocument();
  expect(screen.getByText(/1.5 KiB/)).toBeInTheDocument();
});

test("a removed row offers no selection and no actions", async () => {
  renderPanel();
  const removedRow = (await screen.findByText("gone.md")).closest("tr")!;
  expect(within(removedRow).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(within(removedRow).queryByRole("button", { name: "刪除" })).not.toBeInTheDocument();
});

test("uploader and delete are disabled with a reason while indexing", async () => {
  renderPanel({ activeJob: { id: "j1", type: "index" } });
  expect(await screen.findByText(/索引作業執行中，暫停文件異動/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "刪除所選" })).toBeDisabled();
});

test("clicking a row name opens the preview drawer with the head window", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("notes.txt"));
  expect(await screen.findByText("PREVIEW-BODY")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- FilesPanel`
Expected: FAIL — no bulk toolbar, no drawer.

- [ ] **Step 3: Implement**

Row selection uses antd's `rowSelection` with
`getCheckboxProps: (f) => ({ disabled: f.index_state === "removed" })`.
The frozen state comes from the jobs query already available in the project
(reuse `GET /api/projects/{id}/jobs?...` or the preflight `active_job`) and
disables the dragger and every mutating action **with the reason shown**,
rather than letting the user discover the 409.

- [ ] **Step 4: Both locales, then the frontend gate**

Run: `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`

- [ ] **Step 5: Documentation and release notes**

- `README.md`: the knowledge-manager document workflow; mirror into
  `docs/zh-TW/README.md` **in the same commit**.
- Release notes cover three visible changes from this slice: the input
  freeze on upload/delete/bulk delete, the new 409 on `PUT .../settings`,
  `PATCH .../env` and `DELETE .../env/{key}`, and the nullable
  `FileEntryOut.size`/`modified_at`/`sha256` with rows that have no file
  behind them. State that a trustworthy baseline comes from a full `index`
  and that an `update` will not create one — existing projects read every
  file as `new` until then.
- No new environment variables, so `.env.example`, compose files and the
  Helm chart are untouched.

- [ ] **Step 6: Full gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
cd ../frontend && npm test && npm run lint && npx tsc -b --noEmit && npm run build
git add frontend/src README.md docs/zh-TW
git commit -m "$(cat <<'EOF'
feat(kb): add bulk actions, frozen-state affordances and document preview

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

## Slice exit criteria

Stopping here leaves a coherent product: every input file knows its own
index state and says so when it cannot, the input freeze protects that
answer, and documents are searchable, taggable, bulk-operable and
previewable at a scale of hundreds. Slice ② (`docs/superpowers/plans/2026-09-06-kb-slice2-retrieval-testing.md`)
adds question sets, batch runs and the rating matrix; slice ③
(`…-kb-slice3-wiring.md`) adds the routed sidebar, the health overview, and
the citation-to-passage loop that consumes Task 9's reserved `locator` prop.
