"""project_files.mtime_ns: the listing's hash cache key

Every file listing re-hashed the whole of input/ (R1-69). The project_files
row already stores sha256 and size; with the file's mtime beside them it
becomes the cache the listing reads: a file whose (size, mtime_ns) still
match its row is not hashed again. Nullable — NULL means "not cached"
(existing rows, uploads, files modified too recently to trust), and the
next listing fills it in.

Revision ID: 9c5e1f7a2b64
Revises: d41c7e2b9a15
"""

import sqlalchemy as sa
from alembic import op

revision = "9c5e1f7a2b64"
down_revision = "d41c7e2b9a15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_files", sa.Column("mtime_ns", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_files", "mtime_ns")
