"""refresh token families: family_id and family_created_at

Rotation renewed the 7-day window on every use, so a session that kept
refreshing never expired (R2-26). Every token now carries the family its
login started and the family's start time; rotation caps expires_at at
family_created_at + 30 days (decision D4).

Existing rows backfill as one-token families that start at the row's own
created_at: the start of their real chain is not recorded, so a session
alive at upgrade time gets at most 30 days from its last rotation.

Revision ID: b7e2c4a91d30
Revises: e46955e86d60
Create Date: 2026-09-24

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "b7e2c4a91d30"
down_revision = "e46955e86d60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("family_id", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "refresh_tokens",
        sa.Column("family_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE refresh_tokens SET family_id = id, family_created_at = created_at")
    op.alter_column("refresh_tokens", "family_id", nullable=False)
    op.alter_column("refresh_tokens", "family_created_at", nullable=False)


def downgrade() -> None:
    op.drop_column("refresh_tokens", "family_created_at")
    op.drop_column("refresh_tokens", "family_id")
