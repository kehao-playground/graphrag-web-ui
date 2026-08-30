import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import RefreshToken, Role, User, UserRole
from graphrag_ui.config import get_settings
from graphrag_ui.domain.role_catalog import ROLE_ID_OPS, ROLE_ID_USER_ADMIN
from graphrag_ui.services.audit import audit

logger = logging.getLogger(__name__)

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (Argon2Error, ValueError):  # VerifyMismatchError / InvalidHashError
        return False


# JIT rows have no usable password: this literal never parses as an argon2
# hash, so verify_password is always False — flipping AUTH_MODE back to
# local cannot let a proxy-provisioned account sign in without a reset
# (spec §5.2).
UNUSABLE_PASSWORD_HASH = "!proxy-no-local-password"


async def get_or_provision_user(
    session: AsyncSession, email: str, display_name: str
) -> User:
    """Case-insensitive get-or-create for proxy-mode identity (spec §5.2).

    Legacy rows may store the local part with its original case (create_user
    keeps EmailStr's normalization, which only lowercases the domain), so the
    lookup lowercases both sides; new rows are written lowercased and the
    data converges without a migration. Concurrent first logins race on the
    users.email unique index; the loser rolls back and returns the winner's
    row — the rollback is safe because identity resolution is the first
    thing touching this request's session.
    """
    addr = email.strip().lower()
    settings = get_settings()

    async def _lookup() -> User | None:
        return (await session.execute(
            select(User).where(func.lower(User.email) == addr))).scalar_one_or_none()

    user = await _lookup()
    if user is None:
        user = User(
            email=addr,
            display_name=display_name,
            password_hash=UNUSABLE_PASSWORD_HASH,
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        try:
            await session.flush()
            if addr in settings.proxy_admin_set:
                session.add_all([
                    UserRole(user_id=user.id, role_id=ROLE_ID_USER_ADMIN),
                    UserRole(user_id=user.id, role_id=ROLE_ID_OPS)])
            await audit(session, user.id, "user.created", "user", str(user.id),
                        payload={"email": addr, "origin": "proxy-jit"})
            await session.commit()
        except IntegrityError:
            # Lost the insert race: the unique-index winner's row is
            # committed (PG blocks our insert until theirs resolves).
            await session.rollback()
            user = await _lookup()
            assert user is not None

    # Authoritative-upward reconciliation (spec §5.2 / decision 7): a
    # listed email is granted whatever part of the composition it lacks,
    # on every resolve. Grant-set difference, not role-name equality —
    # a user holding only user_admin gets ops added, and so on.
    if addr in settings.proxy_admin_set:
        have = set((await session.execute(
            select(UserRole.role_id).where(UserRole.user_id == user.id)
        )).scalars().all())
        missing = [rid for rid in (ROLE_ID_USER_ADMIN, ROLE_ID_OPS)
                   if rid not in have]
        if missing:
            session.add_all([UserRole(user_id=user.id, role_id=rid)
                             for rid in missing])
            await audit(session, user.id, "user.role_promoted", "user",
                        str(user.id), payload={"via": "proxy_admin_emails"})
            await session.commit()
    return user

def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user.id), "type": "access",
         "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes)},
        s.jwt_secret, algorithm="HS256")


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> str:
    s = get_settings()
    token = secrets.token_urlsafe(48)
    session.add(RefreshToken(
        user_id=user_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days)))
    await session.commit()
    return token


async def _find(session: AsyncSession, token: str) -> RefreshToken | None:
    h = hashlib.sha256(token.encode()).hexdigest()
    return (await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == h))).scalar_one_or_none()


async def rotate_refresh(session: AsyncSession, token: str) -> tuple[uuid.UUID, str] | None:
    """Returns (user_id, new_refresh); None on failure. The caller issues the access token from user_id."""
    row = await _find(session, token)
    if row is None:
        return None
    if row.revoked_at is not None:
        # A consumed token reappearing = suspected leak → revoke the user's entire token family
        await revoke_all_for_user(session, row.user_id)
        return None
    if row.expires_at < datetime.now(UTC):
        return None
    row.revoked_at = datetime.now(UTC)   # mark instead of delete, so reuse detection works
    await session.commit()
    return row.user_id, await issue_refresh_token(session, row.user_id)


async def revoke_refresh(session: AsyncSession, token: str) -> None:
    row = await _find(session, token)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    await session.commit()


_DUMMY_HASH = _ph.hash("dummy-for-constant-time")


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)   # flatten response time; prevents account enumeration via timing
        return None
    return user if verify_password(password, user.password_hash) else None


async def bootstrap_admin(session: AsyncSession) -> None:
    if get_settings().auth_mode == "proxy":
        # Proxy mode: the initial admin comes from PROXY_ADMIN_EMAILS JIT
        # (spec §5.2); local login is disabled, so a password-having admin
        # would be unreachable anyway.
        return
    s = get_settings()
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        return
    # Probe by EFFECTIVE permission, not role name, and never with a
    # scalar_one_or_none(): multiple admins raise MultipleResultsFound —
    # a startup crash. user_admin is expected to have several holders.
    holder = (await session.execute(
        select(User.email)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(User.is_active.is_(True),
               Role.permissions.contains(["users:manage"]))
        .limit(1))).scalar_one_or_none()
    if holder is not None:
        # Silently returning here is the most confusing trial failure: the
        # .env password was changed between runs, the persisted admin keeps
        # the old one, and login just says "invalid email or password".
        logger.warning(
            "Bootstrap admin %s skipped: %s already holds users:manage "
            "(BOOTSTRAP_ADMIN_PASSWORD is ignored; to recreate the admin: "
            "docker compose down -v — destroys all data).",
            s.bootstrap_admin_email, holder)
        return
    admin = User(email=s.bootstrap_admin_email,
                 password_hash=hash_password(s.bootstrap_admin_password),
                 display_name="Administrator",
                 is_active=True, must_change_password=True)
    session.add(admin)
    await session.flush()
    session.add_all([UserRole(user_id=admin.id, role_id=ROLE_ID_USER_ADMIN),
                     UserRole(user_id=admin.id, role_id=ROLE_ID_OPS)])
    await audit(session, None, "user.created", "user", str(admin.id),
                payload={"email": s.bootstrap_admin_email,
                         "origin": "bootstrap"})
    await session.commit()
