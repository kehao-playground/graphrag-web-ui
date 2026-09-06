"""kb document governance: project files, tags, index snapshots

Revision ID: 2cc9c6fac7ae
Revises: a3d81f0c6b52
Create Date: 2026-09-06

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "2cc9c6fac7ae"
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
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
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

    op.add_column("projects", sa.Column("baseline_snapshot_id", UUID(as_uuid=True), nullable=True))
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
