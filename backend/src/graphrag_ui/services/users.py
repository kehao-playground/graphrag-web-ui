import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import User
from graphrag_ui.services.audit import audit
from graphrag_ui.services.auth import hash_password, revoke_all_for_user


class UserNotFound(LookupError):
    """No user exists for the requested id."""


class SelfRoleChangeError(ValueError):
    """An admin tried to change their own role or active status."""


class LastActiveAdminError(ValueError):
    """The change would demote or deactivate the last remaining active admin."""


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    return user


async def list_users_ordered(session: AsyncSession) -> list[User]:
    # Explicit ordering — tests and frontend must not rely on implicit DB row order.
    return list((await session.execute(
        select(User).order_by(User.created_at, User.id))).scalars().all())


async def list_users_by_email(session: AsyncSession) -> list[User]:
    return list((await session.execute(
        select(User).order_by(User.email))).scalars().all())


async def _other_active_admin_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (await session.execute(
        select(func.count()).select_from(User).where(
            User.role == "admin", User.is_active.is_(True), User.id != user_id)
    )).scalar_one()


async def create_user(session: AsyncSession, email: str, display_name: str,
                      password: str, actor_id: uuid.UUID | None) -> User:
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        # An admin-set initial password must not live long — same semantics as reset_password
        must_change_password=True,
    )
    session.add(user)
    await session.flush()  # produce user.id for the audit target_id
    await audit(session, actor_id, "user.created", "user", str(user.id),
                payload={"email": email})
    await session.commit()
    return user


async def update_user(session: AsyncSession, user: User, *, display_name: str | None = None,
                      role: str | None = None, is_active: bool | None = None,
                      actor_id: uuid.UUID | None) -> User:
    changed: dict = {}
    if display_name is not None and display_name != user.display_name:
        user.display_name = display_name
        changed["display_name"] = display_name
    if role is not None and role != user.role:
        user.role = role
        changed["role"] = role
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


async def reset_password(session: AsyncSession, user: User, new_password: str,
                         actor_id: uuid.UUID | None) -> None:
    user.password_hash = hash_password(new_password)
    user.must_change_password = True  # an admin reset likewise forces a change at next login
    await audit(session, actor_id, "user.password_reset", "user", str(user.id))
    await revoke_all_for_user(session, user.id)  # commits internally, flushing password change + audit
    await session.commit()


async def patch_user_guarded(session: AsyncSession, admin: User, user_id: uuid.UUID, *,
                             display_name: str | None, role: str | None,
                             is_active: bool | None) -> User:
    user = await get_user(session, user_id)
    if user.id == admin.id and (role is not None or is_active is not None):
        raise SelfRoleChangeError("cannot change your own role or active status")
    demotes = role is not None and role != "admin"
    # Demoting or deactivating the last active admin would lock the system out.
    if (user.role == "admin" and user.is_active and (demotes or is_active is False)
            and await _other_active_admin_count(session, user.id) == 0):
        raise LastActiveAdminError("cannot demote or deactivate the last active admin")
    return await update_user(session, user, display_name=display_name, role=role,
                             is_active=is_active, actor_id=admin.id)
