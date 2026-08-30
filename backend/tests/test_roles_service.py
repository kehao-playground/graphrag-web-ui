"""services/roles.py: validation, system protection, in-use, guards."""
import uuid

import pytest

from graphrag_ui.adapters.models import Project, ProjectMember, User, UserRole
from graphrag_ui.domain.role_catalog import (
    ROLE_ID_OPS,
    ROLE_ID_OWNER,
    ROLE_ID_USER_ADMIN,
    ROLE_ID_VIEWER,
)
from graphrag_ui.services import roles as svc


async def _user(db, email="u@x.com", active=True):
    u = User(email=email, password_hash="h", display_name=email,
             is_active=active, must_change_password=False)
    db.add(u)
    await db.flush()
    return u


async def _project(db, owner):
    p = Project(name="P", slug=f"p-{uuid.uuid4().hex[:8]}", owner_id=owner.id,
                input_file_type="text")
    db.add(p)
    await db.flush()
    # `role` is the legacy column: still NOT NULL until R2 (Task 4) drops
    # it. Every ProjectMember built before Task 4 must set BOTH columns.
    db.add(ProjectMember(project_id=p.id, user_id=owner.id, role="owner",
                         role_id=ROLE_ID_OWNER))
    await db.flush()
    return p


async def test_create_role_validates_scope(db_session):
    u = await _user(db_session)
    with pytest.raises(svc.RoleScopeMismatchError):
        await svc.create_role(db_session, scope="team", name="x",
                              description="", permissions=[], actor_id=u.id)


async def test_create_role_rejects_wrong_scope_atoms(db_session):
    u = await _user(db_session)
    # a global role may not carry project atoms (spec §5.3)
    with pytest.raises(svc.RolePermissionsInvalidError):
        await svc.create_role(db_session, scope="global", name="auditor",
                              description="",
                              permissions=["project:view"], actor_id=u.id)
    # ...and a project role may not carry global atoms
    with pytest.raises(svc.RolePermissionsInvalidError):
        await svc.create_role(db_session, scope="project", name="weird",
                              description="",
                              permissions=["projects:view_any"],
                              actor_id=u.id)


async def test_create_role_rejects_unknown_atom(db_session):
    u = await _user(db_session)
    with pytest.raises(svc.RolePermissionsInvalidError):
        await svc.create_role(db_session, scope="global", name="x",
                              description="",
                              permissions=["users:manage", "not:an_atom"],
                              actor_id=u.id)


async def test_create_role_name_unique_per_scope(db_session):
    u = await _user(db_session)
    await svc.create_role(db_session, scope="global", name="auditor",
                          description="", permissions=["projects:view_any"],
                          actor_id=u.id)
    # same name, same scope -> rejected; same name, other scope -> allowed
    with pytest.raises(svc.RoleNameTakenError, match="auditor"):
        await svc.create_role(db_session, scope="global", name="auditor",
                              description="", permissions=[],
                              actor_id=u.id)
    await svc.create_role(db_session, scope="project", name="auditor",
                          description="", permissions=["project:view"],
                          actor_id=u.id)


async def test_system_roles_are_immutable(db_session):
    u = await _user(db_session)
    role = await svc.get_role(db_session, ROLE_ID_VIEWER)
    with pytest.raises(svc.RoleIsSystemError):
        await svc.update_role(db_session, role, name="viewer2",
                              description="", permissions=[],
                              actor_id=u.id)
    with pytest.raises(svc.RoleIsSystemError):
        await svc.delete_role(db_session, role, actor_id=u.id)


async def test_delete_role_in_use_rejected(db_session):
    u = await _user(db_session)
    p = await _project(db_session, u)
    # a SECOND user: _project already inserted the owner's member row and
    # (project_id, user_id) is the PK — reusing `u` is a duplicate key
    member = await _user(db_session, "member@x.com")
    custom = await svc.create_role(
        db_session, scope="project", name="auditor", description="",
        permissions=["project:view"], actor_id=u.id)
    db_session.add(ProjectMember(project_id=p.id, user_id=member.id,
                                 role="viewer", role_id=custom.id))
    await db_session.commit()
    with pytest.raises(svc.RoleInUseError):
        await svc.delete_role(db_session, custom, actor_id=u.id)
    # unused custom role deletes fine
    other = await svc.create_role(db_session, scope="global", name="empty",
                                  description="", permissions=[],
                                  actor_id=u.id)
    await svc.delete_role(db_session, other, actor_id=u.id)


async def test_update_role_dropping_users_manage_guarded(db_session):
    # two users: one holds users:manage ONLY via the custom role
    holder = await _user(db_session, "holder@x.com")
    db_session.add(UserRole(user_id=holder.id, role_id=ROLE_ID_USER_ADMIN))
    custom = await svc.create_role(
        db_session, scope="global", name="helper", description="",
        permissions=["users:manage"], actor_id=holder.id)
    db_session.add(UserRole(user_id=holder.id, role_id=custom.id))
    await db_session.commit()
    # stripping users:manage from the custom role would leave exactly one
    # active manager (the direct user_admin holder) -> allowed
    await svc.update_role(db_session, custom, name="helper", description="",
                          permissions=[], actor_id=holder.id)
    # now make it the LAST source: drop the direct grant too
    await db_session.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == holder.id,
            UserRole.role_id == ROLE_ID_USER_ADMIN))
    custom2 = await svc.create_role(
        db_session, scope="global", name="last", description="",
        permissions=["users:manage"], actor_id=holder.id)
    db_session.add(UserRole(user_id=holder.id, role_id=custom2.id))
    await db_session.commit()
    with pytest.raises(svc.LastUserManagerError):
        await svc.update_role(db_session, custom2, name="last",
                              description="", permissions=[],
                              actor_id=holder.id)


async def test_usage_counts_and_roles_for_user(db_session):
    u = await _user(db_session)
    p = await _project(db_session, u)
    member = await _user(db_session, "member@x.com")  # see the note above
    custom = await svc.create_role(db_session, scope="project", name="aud",
                                   description="",
                                   permissions=["project:view"],
                                   actor_id=u.id)
    db_session.add(ProjectMember(project_id=p.id, user_id=member.id,
                                 role="viewer", role_id=custom.id))
    db_session.add(UserRole(user_id=u.id, role_id=ROLE_ID_OPS))
    await db_session.commit()
    counts = await svc.usage_counts(db_session)
    assert counts[custom.id] == {"users": 0, "members": 1}
    assert counts[ROLE_ID_OPS] == {"users": 1, "members": 0}
    assert counts[ROLE_ID_OWNER] == {"users": 0, "members": 1}
    names = {r.name for r in await svc.roles_for_user(db_session, u.id)}
    assert names == {"ops"}


async def test_load_roles_and_global_scope_validation(db_session):
    await _user(db_session)
    with pytest.raises(svc.RoleNotFound):
        await svc.load_roles(db_session, [uuid.uuid4()])
    with pytest.raises(svc.RoleScopeMismatchError):
        svc.validate_global_roles(  # plain function — never awaited
            [await svc.get_role(db_session, ROLE_ID_VIEWER)])
    svc.validate_global_roles([await svc.get_role(db_session, ROLE_ID_OPS)])
