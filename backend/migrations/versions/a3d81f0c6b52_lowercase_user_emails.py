"""lowercase user emails and enforce it case-insensitively

Local login compared users.email raw while proxy-mode identity resolution
lowercased both sides, so an account created as `Alice@corp.com` could not
be logged into as `alice@corp.com`. The code now normalizes on write and
compares case-insensitively; this migration brings existing rows in line and
adds the functional unique index that keeps them there — without it, one
regression on the write path re-admits two rows for one address, and the
case-insensitive lookup then fails with MultipleResultsFound (a 500) rather
than a constraint violation.

⚠ If a database already holds two rows whose emails differ only in case,
the UPDATE below violates the existing unique index and this migration
fails. That is deliberate: silently merging or dropping one of two real
accounts is not a decision a migration should make. Resolve the duplicate
(decide which account survives, reassign its project memberships) and
re-run.

Revision ID: a3d81f0c6b52
Revises: 5e788ac7d4ad
"""

from alembic import op

revision = "a3d81f0c6b52"
down_revision = "5e788ac7d4ad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email))")
    op.execute("CREATE UNIQUE INDEX ix_users_email_lower ON users (lower(email))")


def downgrade() -> None:
    # The lowercasing is not reversible (the original case is gone); only the
    # index is dropped.
    op.execute("DROP INDEX IF EXISTS ix_users_email_lower")
