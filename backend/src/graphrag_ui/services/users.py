import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Role, User, UserRole
from graphrag_ui.services.audit import audit
from graphrag_ui.services.auth import hash_password, revoke_all_for_user
from graphrag_ui.services.roles import (
    LastUserManagerError,
    load_roles,
    other_active_manager_count,
    roles_for_user,
    validate_global_roles,
)


class UserNotFound(LookupError):
    """No user exists for the requested id."""


class SelfRoleChangeError(ValueError):
    """A users:manage holder tried to change their own roles or active status."""


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    return user


async def list_users_with_roles(session: AsyncSession) -> list[tuple[User, list[Role]]]:
    rows = (
        await session.execute(
            select(User, Role)
            .outerjoin(UserRole, UserRole.user_id == User.id)
            .outerjoin(Role, Role.id == UserRole.role_id)
            .order_by(User.created_at, User.id)
        )
    ).all()
    out: list[tuple[User, list[Role]]] = []
    for user, role in rows:
        if not out or out[-1][0].id != user.id:
            out.append((user, []))
        if role is not None:
            out[-1][1].append(role)
    return out


async def list_users_by_email(session: AsyncSession) -> list[User]:
    return list((await session.execute(select(User).order_by(User.email))).scalars().all())


async def create_user(
    session: AsyncSession,
    email: str,
    display_name: str,
    password: str,
    role_ids: list[uuid.UUID] | None,
    actor_id: uuid.UUID | None,
) -> User:
    roles = await load_roles(session, role_ids or [])
    validate_global_roles(roles)
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        # An admin-set initial password must not live long — same semantics as reset_password
        must_change_password=True,
    )
    session.add(user)
    await session.flush()  # produce user.id for the audit target_id
    for r in roles:
        session.add(UserRole(user_id=user.id, role_id=r.id))
    await audit(
        session,
        actor_id,
        "user.created",
        "user",
        str(user.id),
        payload={"email": email, "roles": [r.name for r in roles]},
    )
    await session.commit()
    return user


async def _user_grant_names(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    return sorted(r.name for r in await roles_for_user(session, user_id))


async def update_user(
    session: AsyncSession,
    user: User,
    *,
    display_name: str | None = None,
    role_ids: list[uuid.UUID] | None = None,
    is_active: bool | None = None,
    actor_id: uuid.UUID | None,
) -> User:
    changed: dict = {}
    if display_name is not None and display_name != user.display_name:
        user.display_name = display_name
        changed["display_name"] = display_name
    if role_ids is not None:
        roles = await load_roles(session, role_ids)
        validate_global_roles(roles)
        names = sorted(r.name for r in roles)
        if names != await _user_grant_names(session, user.id):
            await session.execute(sa_delete(UserRole).where(UserRole.user_id == user.id))
            for r in roles:
                session.add(UserRole(user_id=user.id, role_id=r.id))
            changed["roles"] = names
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        changed["is_active"] = is_active
    if not changed:  # an empty PATCH is not a write; no audit
        return user
    await audit(session, actor_id, "user.updated", "user", str(user.id), payload=changed)
    if changed.get("is_active") is False:
        # Deactivation revokes all refresh tokens; revoke_all_for_user commits
        # internally, flushing the user mutation and audit record above too.
        await revoke_all_for_user(session, user.id)
    await session.commit()
    return user


async def reset_password(
    session: AsyncSession, user: User, new_password: str, actor_id: uuid.UUID | None
) -> None:
    user.password_hash = hash_password(new_password)
    user.must_change_password = True  # an admin reset likewise forces a change at next login
    await audit(session, actor_id, "user.password_reset", "user", str(user.id))
    await revoke_all_for_user(
        session, user.id
    )  # commits internally, flushing password change + audit
    await session.commit()


async def _holds_users_manage(session: AsyncSession, user_id: uuid.UUID) -> bool:
    return (
        await session.execute(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, Role.permissions.contains(["users:manage"]))
        )
    ).scalar_one() > 0


async def _loses_last_manager(
    session: AsyncSession, user: User, role_ids: list[uuid.UUID] | None, is_active: bool | None
) -> bool:
    """True when this mutation would take the system to zero ACTIVE
    users:manage holders (spec §6.2). Only the target's loss matters:
    if they keep the atom post-change, nothing is lost."""
    if not await _holds_users_manage(session, user.id):
        return False
    keeps = is_active is not False
    if role_ids is not None:
        roles = await load_roles(session, role_ids)
        validate_global_roles(roles)
        keeps = keeps and any("users:manage" in (r.permissions or []) for r in roles)
    if keeps:
        return False
    return await other_active_manager_count(session, user.id) == 0


async def patch_user_guarded(
    session: AsyncSession,
    actor: User,
    actor_perms: frozenset[str],
    user_id: uuid.UUID,
    *,
    display_name: str | None,
    role_ids: list[uuid.UUID] | None,
    is_active: bool | None,
) -> User:
    user = await get_user(session, user_id)
    if user.id == actor.id and (role_ids is not None or is_active is not None):
        raise SelfRoleChangeError("cannot change your own roles or active status")
    if await _loses_last_manager(session, user, role_ids, is_active):
        raise LastUserManagerError("cannot remove the last active holder of users:manage")
    return await update_user(
        session,
        user,
        display_name=display_name,
        role_ids=role_ids,
        is_active=is_active,
        actor_id=actor.id,
    )
