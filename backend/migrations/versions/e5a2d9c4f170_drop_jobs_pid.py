"""drop jobs.pid: never written

The column was meant to carry the indexing subprocess's pid, but the runner
never recorded it (the spawned pid is logged instead, and a pid from another
pod is meaningless), so every row holds NULL (R1-39).

Revision ID: e5a2d9c4f170
Revises: 9c5e1f7a2b64
"""

import sqlalchemy as sa
from alembic import op

revision = "e5a2d9c4f170"
down_revision = "9c5e1f7a2b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("jobs", "pid")


def downgrade() -> None:
    op.add_column("jobs", sa.Column("pid", sa.Integer(), nullable=True))
