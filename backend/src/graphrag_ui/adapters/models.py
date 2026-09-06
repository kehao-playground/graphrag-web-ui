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
    type: Mapped[str] = mapped_column(String(10))  # index|update
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
