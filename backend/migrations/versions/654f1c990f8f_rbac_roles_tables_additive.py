"""rbac roles tables additive (R1 of spec §5.2)

Creates roles + user_roles, seeds the six built-in roles by fixed id,
backfills user_roles from users.role and project_members.role_id from the
role strings, and KEEPS both legacy columns (dropped by R2 after the code
cutover). UUID literals below must match domain/role_catalog.py verbatim.

Revision ID: 654f1c990f8f
Revises: 47b77c99bc8f
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "654f1c990f8f"
down_revision = "47b77c99bc8f"
branch_labels = None
depends_on = None

ROLE_IDS = {
    "user_admin": "00000000-0000-4000-8000-000000000001",
    "ops": "00000000-0000-4000-8000-000000000002",
    "viewer": "00000000-0000-4000-8000-000000000003",
    "maintainer": "00000000-0000-4000-8000-000000000004",
    "editor": "00000000-0000-4000-8000-000000000005",
    "owner": "00000000-0000-4000-8000-000000000006",
}


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("permissions", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("scope", "name", name="uq_roles_scope_name"),
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    op.add_column("project_members", sa.Column("role_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_project_members_role_id", "project_members", ["role_id"])
    op.create_foreign_key(
        "fk_project_members_role_id",
        "project_members",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Seed built-ins by fixed id; idempotent on re-run / partially-seeded DBs.
    # UUIDs are inlined (f-string), not bound params: asyncpg's UUID codec
    # rejects String-typed bindparams ("Comparator has no attribute 'bytes'").
    op.execute(
        sa.text(f"""
        INSERT INTO roles (id, scope, name, description, permissions,
                           is_system, created_at)
        VALUES
          ('{ROLE_IDS["user_admin"]}', 'global', 'user_admin',
           'Manage users and roles', ARRAY['users:manage'], true, now()),
          ('{ROLE_IDS["ops"]}', 'global', 'ops',
           'Operate every project', ARRAY['projects:view_any',
           'projects:act_any'], true, now()),
          ('{ROLE_IDS["viewer"]}', 'project', 'viewer',
           'Read-only access', ARRAY['project:view'], true, now()),
          ('{ROLE_IDS["maintainer"]}', 'project', 'maintainer',
           'Curate documents and run indexing',
           ARRAY['project:view', 'project:edit_content',
                 'project:run_jobs'], true, now()),
          ('{ROLE_IDS["editor"]}', 'project', 'editor',
           'Maintainer plus settings and API keys',
           ARRAY['project:view', 'project:edit_content', 'project:run_jobs',
                 'project:edit_settings'], true, now()),
          ('{ROLE_IDS["owner"]}', 'project', 'owner',
           'Full control of the project',
           ARRAY['project:view', 'project:edit_content', 'project:run_jobs',
                 'project:edit_settings', 'project:manage'], true, now())
        ON CONFLICT (id) DO NOTHING
    """)
    )

    # Legacy admins gain exactly [user_admin, ops]; plain users gain nothing
    # (projects:create is a code constant, spec §4.1)
    op.execute(
        sa.text(f"""
        INSERT INTO user_roles (user_id, role_id)
        SELECT u.id, '{ROLE_IDS["user_admin"]}'
        FROM users u WHERE u.role = 'admin'
        ON CONFLICT DO NOTHING
    """)
    )
    op.execute(
        sa.text(f"""
        INSERT INTO user_roles (user_id, role_id)
        SELECT u.id, '{ROLE_IDS["ops"]}'
        FROM users u WHERE u.role = 'admin'
        ON CONFLICT DO NOTHING
    """)
    )
    op.execute(
        sa.text(f"""
        UPDATE project_members SET role_id = CASE role
          WHEN 'owner' THEN '{ROLE_IDS["owner"]}'::uuid
          WHEN 'editor' THEN '{ROLE_IDS["editor"]}'::uuid
          WHEN 'viewer' THEN '{ROLE_IDS["viewer"]}'::uuid
        END
        WHERE role_id IS NULL
    """)
    )


def downgrade() -> None:
    """Custom roles vanish with the tables — stated loss (spec §5.2)."""
    op.drop_index("ix_project_members_role_id", table_name="project_members")
    op.drop_constraint("fk_project_members_role_id", "project_members", type_="foreignkey")
    op.drop_column("project_members", "role_id")
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("roles")
