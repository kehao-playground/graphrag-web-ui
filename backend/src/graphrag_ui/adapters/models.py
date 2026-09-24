import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    """A named set of permission atoms (spec §4). Built-ins are seeded by
    fixed id and are immutable (is_system); custom roles are admin-created."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("scope", "name", name="uq_roles_scope_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(10))  # global|project
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(200), default="")
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text()), default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRole(Base):
    """Global role grant. Scope (global) is enforced in the service layer —
    a CHECK cannot span tables without triggers, which we do not add."""

    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # One family per login; every rotation inherits both fields, and
    # family_created_at bounds the whole chain (services/auth.py).
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    family_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    input_file_type: Mapped[str] = mapped_column(String(10))  # text|csv|json
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    baseline_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # use_alter breaks the projects <-> index_snapshots FK cycle for
        # metadata sorting; without it Base.metadata.sorted_tables raises
        # CircularDependencyError and conftest's TRUNCATE fixture dies.
        ForeignKey(
            "index_snapshots.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_baseline_snapshot",
        ),
        nullable=True,
    )
    artifact_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProjectMember(Base):
    __tablename__ = "project_members"
    # Both FKs CASCADE: the DB handles member cleanup on project/user deletion;
    # services never delete members manually
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT")
    )


class SettingsVersion(Base):
    """Snapshot of settings.yaml written by write_settings(); restore re-snapshots."""

    __tablename__ = "settings_versions"
    # project FK CASCADE follows project_members: deleting the project drops its history
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex
    saved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """One queued/executed graphrag index|update run (spec §5).

    Per-project mutual exclusion is enforced by the partial unique index
    jobs_one_active_per_project (migration); application logic never
    checks-and-inserts (race-prone) — it inserts and maps IntegrityError.
    """

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(10))  # index|update|test_run
    method: Mapped[str] = mapped_column(String(16))  # standard|fast
    argv: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    queued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A test_run job stores {"run_id"}; the ordered question manifest lives
    # in the test_results rows written by the same transaction, not here —
    # argv stays a graphrag CLI argument vector (empty for test_run).
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ProjectFile(Base):
    """Metadata for one file in input/. NOT the source of truth for
    existence - input/ on disk is (spec 5.1); a delete removes the row with
    the file, and an untracked file is discovered into a row on first list."""

    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_project_files_project_name"),)
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


class QuestionSet(Base):
    """A named set of questions. Archived, never hard-deleted on user action:
    a cascade would destroy exactly the run history test_results.question_text
    exists to protect (spec 5.3)."""

    __tablename__ = "question_sets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Question(Base):
    """Immutable once referenced by a run: editing forks a new row on the
    same lineage_id. The matrix's rows are lineages, not question rows, so a
    question keeps one row while each cell shows the text actually asked."""

    __tablename__ = "questions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_sets.id", ondelete="CASCADE"), index=True
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestRun(Base):
    """One batch run of a question set, executed as a test_run job.
    index_job_id records the last successful index/update at run start — it
    labels a matrix column and is what makes runs comparable."""

    __tablename__ = "test_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("question_sets.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    index_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True
    )
    method: Mapped[str] = mapped_column(String(16))
    workspace_config_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestResult(Base):
    """One answer cell of a run, inserted as a nullable placeholder at
    enqueue. question_text is the question AS ASKED, denormalized: a historic
    run is self-contained and an edit cannot retro-label an old answer."""

    __tablename__ = "test_results"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_test_results_run_question"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(Integer)
    question_text: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    timings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResultRating(Base):
    """One CURRENT rating per result, project-shared rather than per-user.

    This is a team console: a maintainer must see the quality judgement
    their colleague recorded. Re-rating overwrites; who changed what is
    carried by the audit log (test.rated), not by row versioning.
    """

    __tablename__ = "result_ratings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"), unique=True
    )
    score: Mapped[str] = mapped_column(String(8))  # good | fair | poor
    note: Mapped[str] = mapped_column(Text, server_default="")
    rated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
