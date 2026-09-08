"""kb question sets and runs: sets, questions, test_runs, test_results, result_ratings

Revision ID: e46955e86d60
Revises: 2cc9c6fac7ae
Create Date: 2026-09-08

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "e46955e86d60"
down_revision = "2cc9c6fac7ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Soft delete: a set referenced by a run is never hard-deleted. A
        # cascade would destroy exactly the history question_text exists to
        # protect (spec 5.3).
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_question_sets_project_id", "question_sets", ["project_id"])

    op.create_table(
        "questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("question_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Stable across edits: the matrix's rows are lineages, not question
        # rows, so a question keeps one row while each cell shows the text
        # actually asked.
        sa.Column("lineage_id", UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_questions_set_id", "questions", ["set_id"])
    op.create_index("ix_questions_lineage_id", "questions", ["lineage_id"])

    op.create_table(
        "test_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_id", UUID(as_uuid=True), sa.ForeignKey("question_sets.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        # The last successful index/update at run start: it labels a matrix
        # column and is what makes runs comparable. Nullable for a project
        # queried before any index job row exists.
        sa.Column("index_job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        # Framed sha256 over settings.yaml AND .env, captured atomically with
        # the config load (spec 7.2).
        sa.Column("workspace_config_revision", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_test_runs_project_id", "test_runs", ["project_id"])

    op.create_table(
        "test_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        # The question AS ASKED, denormalized at enqueue: a historic run is
        # self-contained and an edit cannot retro-label an old answer.
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", JSONB, nullable=True),
        sa.Column("timings", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "question_id", name="uq_test_results_run_question"),
    )
    op.create_index("ix_test_results_run_id", "test_results", ["run_id"])

    op.create_table(
        "result_ratings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_results.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("score", sa.String(8), nullable=False),  # good | fair | poor
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("rated_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "rated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column("jobs", sa.Column("params", JSONB, nullable=True))
    op.add_column("jobs", sa.Column("progress", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "progress")
    op.drop_column("jobs", "params")
    op.drop_table("result_ratings")
    op.drop_index("ix_test_results_run_id", table_name="test_results")
    op.drop_table("test_results")
    op.drop_index("ix_test_runs_project_id", table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_index("ix_questions_lineage_id", table_name="questions")
    op.drop_index("ix_questions_set_id", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_question_sets_project_id", table_name="question_sets")
    op.drop_table("question_sets")
