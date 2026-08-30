"""Role CRUD and validation (spec §5.3, §6.2).

Scope rules and atom-subset rules live here because Postgres CHECKs cannot
span tables (no triggers, spec §5.1). The last-user-manager guard queries
the same permissions @> containment the users service uses.
"""
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import ProjectMember, Role, User, UserRole
from graphrag_ui.domain.permissions import GLOBAL_ATOMS, PROJECT_ATOMS, Atom
from graphrag_ui.services.audit import audit

# The grantable atom catalog per scope (domain.permissions, spec §4.1).
# projects:create is a baseline, never grantable — excluded here.
_ATOMS_BY_SCOPE: dict[str, frozenset[str]] = {
    "global": frozenset(a.value for a in GLOBAL_ATOMS
                        if a is not Atom.projects_create),
    "project": frozenset(a.value for a in PROJECT_ATOMS),
}


class RoleNotFound(LookupError):
    """No role exists for the requested id."""


class RoleIsSystemError(ValueError):
    """The target is a seeded built-in role and is immutable."""


class RoleInUseError(ValueError):
    """The role is still granted to users or assigned to members."""


class RoleScopeMismatchError(ValueError):
    """The role's scope does not fit the requested operation."""


class RoleNameTakenError(ValueError):
    """Another role in the same scope already uses this name."""


class RolePermissionsInvalidError(ValueError):
    """The permission set is not a subset of the scope's atom catalog."""


class LastUserManagerError(ValueError):
    """The change would leave zero active holders of users:manage."""


async def list_roles(session: AsyncSession,
                     scope: str | None = None) -> list[Role]:
    stmt = select(Role).order_by(Role.scope, Role.name)
    if scope is not None:
        stmt = stmt.where(Role.scope == scope)
    return list((await session.execute(stmt)).scalars().all())


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise RoleNotFound(str(role_id))
    return role


def _validate(scope: str, permissions: list[str]) -> None:
    if scope not in _ATOMS_BY_SCOPE:
        raise RoleScopeMismatchError(f"unknown scope {scope!r}")
    allowed = _ATOMS_BY_SCOPE[scope]
    bad = [p for p in permissions if p not in allowed]
    if bad:
        raise RolePermissionsInvalidError(
            f"atoms not valid for scope {scope!r}: {', '.join(sorted(bad))}")


async def _name_taken(session: AsyncSession, scope: str, name: str,
                      exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(Role.id).where(Role.scope == scope, Role.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Role.id != exclude_id)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def create_role(session: AsyncSession, *, scope: str, name: str,
                      description: str, permissions: list[str],
                      actor_id: uuid.UUID | None) -> Role:
    _validate(scope, permissions)
    if await _name_taken(session, scope, name):
        raise RoleNameTakenError(
            f"role name {name!r} already exists in scope {scope!r}")
    role = Role(scope=scope, name=name, description=description,
                permissions=permissions, is_system=False)
    session.add(role)
    await session.flush()
    await audit(session, actor_id, "role.created", "role", str(role.id),
                payload={"scope": scope, "name": name,
                         "permissions": sorted(permissions)})
    await session.commit()
    return role


async def update_role(session: AsyncSession, role: Role, *, name: str,
                      description: str, permissions: list[str],
                      actor_id: uuid.UUID | None) -> Role:
    if role.is_system:
        raise RoleIsSystemError("built-in roles are immutable")
    _validate(role.scope, permissions)  # scope is immutable (spec §5.3)
    if name != role.name and await _name_taken(session, role.scope, name,
                                               exclude_id=role.id):
        raise RoleNameTakenError(
            f"role name {name!r} already exists in scope {role.scope!r}")
    if await would_lose_last_user_manager(session, role,
                                          frozenset(permissions)):
        raise LastUserManagerError(
            "cannot remove the last active source of users:manage")
    role.name = name
    role.description = description
    role.permissions = permissions
    await audit(session, actor_id, "role.updated", "role", str(role.id),
                payload={"name": name, "permissions": sorted(permissions)})
    await session.commit()
    return role


async def delete_role(session: AsyncSession, role: Role, *,
                      actor_id: uuid.UUID | None) -> None:
    if role.is_system:
        raise RoleIsSystemError("built-in roles are immutable")
    counts = (await usage_counts(session)).get(role.id, {})
    if counts.get("users", 0) or counts.get("members", 0):
        raise RoleInUseError("role is still granted; unassign it first")
    await audit(session, actor_id, "role.deleted", "role", str(role.id),
                payload={"scope": role.scope, "name": role.name})
    await session.execute(sa_delete(Role).where(Role.id == role.id))
    await session.commit()


async def usage_counts(session: AsyncSession) -> dict[uuid.UUID, dict[str, int]]:
    """Reference counts per role id: {id: {"users": n, "members": n}}."""
    users = dict((await session.execute(
        select(UserRole.role_id, func.count())
        .group_by(UserRole.role_id))).all())
    members = dict((await session.execute(
        select(ProjectMember.role_id, func.count())
        .where(ProjectMember.role_id.is_not(None))  # nullable until R2
        .group_by(ProjectMember.role_id))).all())
    ids = set(users) | set(members)
    return {rid: {"users": users.get(rid, 0), "members": members.get(rid, 0)}
            for rid in ids}


async def roles_for_user(session: AsyncSession,
                         user_id: uuid.UUID) -> list[Role]:
    return list((await session.execute(
        select(Role).join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.scope, Role.name))).scalars().all())


async def load_roles(session: AsyncSession,
                     role_ids: list[uuid.UUID]) -> list[Role]:
    roles = [await get_role(session, rid) for rid in role_ids]
    return roles


def validate_global_roles(roles: list[Role]) -> None:
    for r in roles:
        if r.scope != "global":
            raise RoleScopeMismatchError(
                f"role {r.name!r} is project-scoped and cannot be granted "
                "to a user")


async def _active_manager_count(
        session: AsyncSession, *,
        exclude_user_id: uuid.UUID | None = None,
        exclude_role_id: uuid.UUID | None = None) -> int:
    """Active users holding users:manage, optionally ignoring one user
    and/or one role as a SOURCE of the atom (spec §6.2). Matching is by
    atom, never by role name — a user can hold it via a custom role."""
    stmt = (select(func.count(func.distinct(User.id)))
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active.is_(True),
                   Role.permissions.contains(["users:manage"])))
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    if exclude_role_id is not None:
        stmt = stmt.where(Role.id != exclude_role_id)
    return (await session.execute(stmt)).scalar_one()


async def other_active_manager_count(session: AsyncSession,
                                     user_id: uuid.UUID) -> int:
    """Active users OTHER than user_id holding users:manage. Task 4's
    users service uses this for the patch-user guard."""
    return await _active_manager_count(session, exclude_user_id=user_id)


async def would_lose_last_user_manager(session: AsyncSession, role: Role,
                                       future_permissions: frozenset[str]) -> bool:
    """True when editing `role` to `future_permissions` would leave zero
    active users:manage holders. Only the edit path calls this — deletion
    is blocked outright by role_in_use.

    ONE query, deliberately no per-holder loop. The question is only
    whether some active user still holds the atom from a role OTHER than
    this one. A loop that asks per holder "does another user hold it?"
    counts users whose sole source is the role being edited, so two
    holders of the only users:manage role each look like the other's
    fallback and the guard waves through an edit that ends at zero
    managers.
    """
    if "users:manage" not in set(role.permissions or ()):
        return False   # this role was never a source of the atom
    if "users:manage" in future_permissions:
        return False   # it stays a source
    return await _active_manager_count(session, exclude_role_id=role.id) == 0
