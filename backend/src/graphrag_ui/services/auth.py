import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import RefreshToken, Role, User, UserRole
from graphrag_ui.config import get_settings
from graphrag_ui.domain.role_catalog import ROLE_ID_OPS, ROLE_ID_USER_ADMIN
from graphrag_ui.services.audit import audit

logger = logging.getLogger(__name__)

_ph = PasswordHasher()


def normalize_email(email: str) -> str:
    """The stored/compared form of an address: stripped and lowercased.

    Single source for every read and write. pydantic's EmailStr only
    lowercases the domain, so without this the local part's case leaked into
    the column and split one person across two rows.
    """
    return email.strip().lower()


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


async def get_or_provision_user(session: AsyncSession, email: str, display_name: str) -> User:
    """Case-insensitive get-or-create for proxy-mode identity (spec §5.2).

    Legacy rows may store the local part with its original case (create_user
    keeps EmailStr's normalization, which only lowercases the domain), so the
    lookup lowercases both sides; new rows are written lowercased and the
    data converges without a migration. Concurrent first logins race on the
    users.email unique index; the loser rolls back and returns the winner's
    row — the rollback is safe because identity resolution is the first
    thing touching this request's session.
    """
    addr = normalize_email(email)
    settings = get_settings()

    async def _lookup() -> User | None:
        return (
            await session.execute(select(User).where(func.lower(User.email) == addr))
        ).scalar_one_or_none()

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
                session.add_all(
                    [
                        UserRole(user_id=user.id, role_id=ROLE_ID_USER_ADMIN),
                        UserRole(user_id=user.id, role_id=ROLE_ID_OPS),
                    ]
                )
            await audit(
                session,
                user.id,
                "user.created",
                "user",
                str(user.id),
                payload={"email": addr, "origin": "proxy-jit"},
            )
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
        have = set(
            (await session.execute(select(UserRole.role_id).where(UserRole.user_id == user.id)))
            .scalars()
            .all()
        )
        missing = [rid for rid in (ROLE_ID_USER_ADMIN, ROLE_ID_OPS) if rid not in have]
        if missing:
            session.add_all([UserRole(user_id=user.id, role_id=rid) for rid in missing])
            await audit(
                session,
                user.id,
                "user.role_promoted",
                "user",
                str(user.id),
                payload={"via": "proxy_admin_emails"},
            )
            await session.commit()
    return user


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=s.access_token_minutes),
        },
        s.jwt_secret,
        algorithm="HS256",
    )


# Absolute lifetime of a refresh-token family (one login's rotation chain):
# rotation slides the 7-day window but never past this (decision D4).
REFRESH_FAMILY_MAX_AGE = timedelta(days=30)
# How long a just-consumed token may be presented again and receive the
# successor it already produced. Covers two tabs (or a retried request)
# refreshing with the same token — the benign double-present that used to
# trip reuse detection and log the user out everywhere (R2-05).
REFRESH_REUSE_GRACE = timedelta(seconds=30)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _successor_of(token: str) -> str:
    """The token a rotation of `token` issues, derived rather than random.

    Only hashes are stored, so a second presenter inside the grace window
    could not otherwise be handed the successor the first one received.
    Keyed with JWT_SECRET: without the secret the successor is as
    unguessable as a random token.
    """
    mac = hmac.new(
        get_settings().jwt_secret.encode(), b"refresh-successor:" + token.encode(), hashlib.sha256
    )
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()


def _add_refresh_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    token: str,
    family_id: uuid.UUID,
    family_created_at: datetime,
    now: datetime,
) -> None:
    sliding = now + timedelta(days=get_settings().refresh_token_days)
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_token_hash(token),
            family_id=family_id,
            family_created_at=family_created_at,
            expires_at=min(sliding, family_created_at + REFRESH_FAMILY_MAX_AGE),
        )
    )


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> str:
    """A fresh token starting a new family (login)."""
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    _add_refresh_token(session, user_id, token, uuid.uuid4(), now, now)
    await session.commit()
    return token


async def _find(session: AsyncSession, token: str) -> RefreshToken | None:
    return (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _token_hash(token))
        )
    ).scalar_one_or_none()


async def rotate_refresh(session: AsyncSession, token: str) -> tuple[uuid.UUID, str] | None:
    """Returns (user_id, new_refresh); None on failure. The caller issues the access token from user_id.

    Consuming the token is one conditional UPDATE, so of two concurrent
    presentations exactly one rotates; the other blocks on the row lock,
    matches nothing, and falls to the grace/reuse branch below (R1-68).
    The revoke and the successor commit together, so a loser that sees the
    revoke also sees the successor.
    """
    now = datetime.now(UTC)
    consumed = (
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == _token_hash(token),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
                RefreshToken.family_created_at > now - REFRESH_FAMILY_MAX_AGE,
            )
            # Mark instead of delete, so reuse detection works
            .values(revoked_at=now)
            .returning(RefreshToken.user_id, RefreshToken.family_id, RefreshToken.family_created_at)
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    successor = _successor_of(token)
    if consumed is not None:
        _add_refresh_token(
            session,
            consumed.user_id,
            successor,
            consumed.family_id,
            consumed.family_created_at,
            now,
        )
        await session.commit()
        return consumed.user_id, successor

    # Columns, not the entity: a statement-level read sees the winner's
    # committed revoke even if this session's identity map holds the row.
    row = (
        await session.execute(
            select(RefreshToken.user_id, RefreshToken.revoked_at).where(
                RefreshToken.token_hash == _token_hash(token)
            )
        )
    ).one_or_none()
    if row is None or row.revoked_at is None:
        # Unknown, or expired / past the family lifetime while unconsumed
        await session.rollback()
        return None
    if now - row.revoked_at <= REFRESH_REUSE_GRACE:
        # A double-present, not a replay, as long as the successor is
        # itself unused: hand out the same successor again.
        live = (
            await session.execute(
                select(RefreshToken.id).where(
                    RefreshToken.token_hash == _token_hash(successor),
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > now,
                )
            )
        ).scalar_one_or_none()
        if live is not None:
            await session.rollback()
            return row.user_id, successor
    # A consumed token reappearing = suspected leak → revoke the user's
    # entire token family
    await revoke_all_for_user(session, row.user_id)
    return None


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
    # Case-insensitive, matching proxy-mode identity resolution
    # (get_or_provision_user) and normalize_email() on the write side. Email
    # local parts are case-insensitive in practice for every mail provider
    # this app talks to, and a raw column comparison meant an account created
    # as `Alice@corp.com` simply could not be logged into as `alice@corp.com`
    # — reported as "invalid email or password", which is unfixable from the
    # UI. Safe as scalar_one_or_none because both the write path and the
    # migration's functional unique index guarantee at most one match.
    user = (
        await session.execute(select(User).where(func.lower(User.email) == normalize_email(email)))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        verify_password(
            password, _DUMMY_HASH
        )  # flatten response time; prevents account enumeration via timing
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
    holder = (
        await session.execute(
            select(User.email)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active.is_(True), Role.permissions.contains(["users:manage"]))
            .limit(1)
        )
    ).scalar_one_or_none()
    if holder is not None:
        # Silently returning here is the most confusing trial failure: the
        # .env password was changed between runs, the persisted admin keeps
        # the old one, and login just says "invalid email or password".
        logger.warning(
            "Bootstrap admin %s skipped: %s already holds users:manage "
            "(BOOTSTRAP_ADMIN_PASSWORD is ignored; to recreate the admin: "
            "docker compose down -v — destroys all data).",
            s.bootstrap_admin_email,
            holder,
        )
        return
    admin = User(
        email=normalize_email(s.bootstrap_admin_email),
        password_hash=hash_password(s.bootstrap_admin_password),
        display_name="Administrator",
        is_active=True,
        must_change_password=True,
    )
    session.add(admin)
    await session.flush()
    session.add_all(
        [
            UserRole(user_id=admin.id, role_id=ROLE_ID_USER_ADMIN),
            UserRole(user_id=admin.id, role_id=ROLE_ID_OPS),
        ]
    )
    await audit(
        session,
        None,
        "user.created",
        "user",
        str(admin.id),
        payload={"email": s.bootstrap_admin_email, "origin": "bootstrap"},
    )
    await session.commit()
