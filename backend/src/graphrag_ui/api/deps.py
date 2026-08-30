import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import Role, User, UserRole
from graphrag_ui.api.errors import ApiError
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Atom
from graphrag_ui.services.auth import get_or_provision_user

_bearer = HTTPBearer(auto_error=False)

# Full set of paths still reachable while must_change_password is true
# (the password-change flow + endpoints that need no login). Single source:
# main.py's global middleware and get_current_user share it; neither may drift.
MUST_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
    "/api/auth/change-password", "/api/auth/me", "/api/auth/config",
    "/api/health", "/api/ready",
})


@dataclass(frozen=True)
class Principal:
    """Request-scoped identity (spec §6.1): the ORM row plus the union of
    the user's global-role atoms, loaded once per request. The delegating
    properties keep route bodies reading `user.id` / `user.email` /
    `user.is_active` / `user.must_change_password` unchanged; guards read
    `global_perms`.

    Read-only on purpose (frozen, properties without setters): any route
    that WRITES to the user row must go through `principal.user`
    (`auth_routes.change_password` is the one such site — see Step 10).
    """
    user: User
    global_perms: frozenset[str]

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def display_name(self) -> str:
        return self.user.display_name

    @property
    def is_active(self) -> bool:
        return self.user.is_active

    @property
    def must_change_password(self) -> bool:
        return self.user.must_change_password


async def load_global_perms(db: AsyncSession,
                            user_id: uuid.UUID) -> frozenset[str]:
    rows = (await db.execute(
        select(Role.permissions)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id))).scalars().all()
    return frozenset().union(*rows) if rows else frozenset()


async def _principal(db: AsyncSession, user: User) -> Principal:
    return Principal(user=user, global_perms=await load_global_perms(db, user.id))


async def get_db():
    # One session per request; the factory itself is a lazy singleton (adapters/db.py)
    async with get_session_factory()() as session:
        yield session


async def resolve_access_user(token: str, db: AsyncSession) -> User | None:
    """Bearer JWT → User; invalid/expired/non-access-type/disabled user → None.

    The token carries no `aud` claim, so decode must not pass `audience=`
    or it will always raise InvalidAudienceError.
    """
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user

# Proxy-mode header identity (spec §5.1). The exactly-one rule via getlist
# is deliberate: different oauth2-proxy versions and ingress controllers
# differ on append-vs-replace for injected headers, and a request whose
# identity is ambiguous is a failed request.
_email_adapter = TypeAdapter(EmailStr)


async def resolve_proxy_user(request: Request, db: AsyncSession) -> Principal:
    """Trusted-header identity for AUTH_MODE=proxy; every failure a 401
    except a disabled account (403, so the SPA shows 'account disabled'
    instead of looping into /oauth2/start)."""
    s = get_settings()
    secrets = request.headers.getlist("X-Proxy-Secret")
    # compare_digest on str raises TypeError when either side is non-ASCII
    # (e.g. a latin-1-decoded header value) — compare bytes so a weird
    # secret header is just a mismatch (401), never a 500. ASGI headers
    # are latin-1; "replace" makes the encode total even for surrogates.
    if len(secrets) != 1 or not hmac.compare_digest(
        secrets[0].encode("latin-1", errors="replace"), s.proxy_auth_secret.encode()
    ):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated")
    emails = request.headers.getlist("X-Forwarded-Email")
    if len(emails) != 1 or len(emails[0]) > 320:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated")
    try:
        email = _email_adapter.validate_python(emails[0])
    except ValidationError:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated") from None
    display = (request.headers.get("X-Forwarded-Preferred-Username") or "").strip()[:100]
    user = await get_or_provision_user(db, email, display or email.split("@")[0])
    if not user.is_active:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth_user_disabled", "account disabled")
    return await _principal(db, user)


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    """Bearer auth dependency shared by every route; every failure is a 401."""
    if get_settings().auth_mode == "proxy":
        return await resolve_proxy_user(request, db)
    if creds is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated")
    user = await resolve_access_user(creds.credentials, db)
    if user is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_invalid_token", "Invalid or expired token")
    # The backend must also enforce the forced password change, not just the frontend modal
    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth_must_change_password", "password change required")
    return await _principal(db, user)


# Shared dependency types for endpoint parameters (FastAPI-conventional
# Annotated aliases, so endpoints don't repeat a long Annotated[...] each)
DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_atom(atom: Atom):
    """Router-level dependency: the caller must hold `atom` globally.
    Keeps the historical `admin_only` error code (spec §7) — only the
    message is reworded toward the permission, away from 'admin'."""
    async def _dep(user: CurrentUser) -> Principal:
        if atom.value not in user.global_perms:
            raise ApiError(status.HTTP_403_FORBIDDEN, "admin_only",
                           "requires user management permission")
        return user
    return _dep


ManageUsers = Annotated[Principal, Depends(require_atom(Atom.users_manage))]

# Auth for SSE routes (job logs, query stream): EventSource cannot send an
# Authorization header, so these routes accept the access token as a ?token=
# query parameter (plan Task 7 decision). Tradeoff: the token then appears in
# access/proxy logs; exposure is bounded by the 15-minute access-token
# rotation. Revisit with short-lived one-time ticket auth when audit
# requirements demand it. Single source — extracted from jobs_routes (Task 4).
_sse_bearer = HTTPBearer(auto_error=False)


async def sse_user_from_request(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_sse_bearer)],
    db: DbSession,
    token: Annotated[str | None, Query()] = None,
) -> Principal:
    """?token= access-token fallback with get_current_user semantics
    (401 invalid/expired, 403 must-change gate) for SSE-only routes."""
    # No tokens exist in proxy mode; the EventSource request carries the
    # oauth2-proxy cookie, so the injected headers are the only credential.
    if get_settings().auth_mode == "proxy":
        return await resolve_proxy_user(request, db)
    if token is not None:
        user = await resolve_access_user(token, db)
        if user is None:
            raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_invalid_token", "Invalid or expired token")
        # Mirror get_current_user's forced-change gate so the ?token= path is
        # not a bypass of that check.
        if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
            raise ApiError(status.HTTP_403_FORBIDDEN, "auth_must_change_password", "password change required")
        return await _principal(db, user)
    # No query token: standard Bearer header semantics.
    return await get_current_user(request, creds, db)


SseUser = Annotated[Principal, Depends(sse_user_from_request)]
