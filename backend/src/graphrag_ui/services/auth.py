import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import RefreshToken, User
from graphrag_ui.config import get_settings

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (Argon2Error, ValueError):  # VerifyMismatchError / InvalidHashError
        return False


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user.id), "role": user.role, "type": "access",
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
    s = get_settings()
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        return
    admin = (await session.execute(select(User).where(User.role == "admin"))).scalar_one_or_none()
    if admin is not None:
        return
    session.add(User(email=s.bootstrap_admin_email, password_hash=hash_password(
        s.bootstrap_admin_password), display_name="Administrator",
        role="admin", must_change_password=True))
    await session.commit()
