"""rbac drop legacy role columns (R2 of spec §5.2)

Backfills role_id stragglers written between R1 and the code cutover,
then drops users.role and project_members.role. Downgrade is LOSSY and
says so (spec §5.2): upgrade-on-downgrade by design — the two-value
column cannot express half-admins, and erring toward 'admin' keeps a
rollback from locking out user management; custom roles vanish with
their grants; custom project roles floor at 'viewer'.

Revision ID: 5e788ac7d4ad
Revises: 654f1c990f8f
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "5e788ac7d4ad"
down_revision = "654f1c990f8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ::uuid casts: asyncpg rejects text-typed CASE branches against a uuid
    # column (same codec note as R1's seed).
    op.execute(
        sa.text("""
        UPDATE project_members SET role_id = CASE role
          WHEN 'owner' THEN '00000000-0000-4000-8000-000000000006'::uuid
          WHEN 'editor' THEN '00000000-0000-4000-8000-000000000005'::uuid
          ELSE '00000000-0000-4000-8000-000000000003'::uuid
        END
        WHERE role_id IS NULL
    """)
    )
    op.alter_column("project_members", "role_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_column("project_members", "role")
    op.drop_column("users", "role")


def downgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(20), nullable=False, server_default="user"))
    # user_admin OR ops -> 'admin': upgrade-on-downgrade, keeps user
    # management reachable during a rollback (spec §5.2)
    op.execute(
        sa.text("""
        UPDATE users SET role = 'admin' WHERE id IN (
          SELECT ur.user_id FROM user_roles ur
          JOIN roles r ON r.id = ur.role_id
          WHERE r.scope = 'global'
            AND r.name IN ('user_admin', 'ops'))
    """)
    )
    op.add_column(
        "project_members", sa.Column("role", sa.String(20), nullable=False, server_default="viewer")
    )
    # built-ins map home; maintainer and custom roles floor at 'viewer' —
    # never silently upgrading a member's power on the way down
    op.execute(
        sa.text("""
        UPDATE project_members pm SET role = COALESCE((
          SELECT CASE r.name
            WHEN 'owner' THEN 'owner'
            WHEN 'editor' THEN 'editor'
            WHEN 'viewer' THEN 'viewer'
            ELSE 'viewer'
          END
          FROM roles r WHERE r.id = pm.role_id), 'viewer')
    """)
    )
    op.alter_column("project_members", "role_id", existing_type=UUID(as_uuid=True), nullable=True)
