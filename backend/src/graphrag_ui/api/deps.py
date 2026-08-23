import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import User
from graphrag_ui.config import get_settings

_bearer = HTTPBearer(auto_error=False)

# Full set of paths still reachable while must_change_password is true
# (the password-change flow + endpoints that need no login). Single source:
# main.py's global middleware and get_current_user share it; neither may drift.
MUST_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
    "/api/auth/change-password", "/api/auth/me",
    "/api/health", "/api/ready",
})


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


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Bearer auth dependency shared by later tasks; every failure is a 401."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = await resolve_access_user(creds.credentials, db)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # The backend must also enforce the forced password change, not just the frontend modal
    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password change required")
    return user


# Shared dependency types for endpoint parameters (FastAPI-conventional
# Annotated aliases, so endpoints don't repeat a long Annotated[...] each)
DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]

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
) -> User:
    """?token= access-token fallback with get_current_user semantics
    (401 invalid/expired, 403 must-change gate) for SSE-only routes."""
    if token is not None:
        user = await resolve_access_user(token, db)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        # Mirror get_current_user's forced-change gate so the ?token= path is
        # not a bypass of that check.
        if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "password change required")
        return user
    # No query token: standard Bearer header semantics.
    return await get_current_user(request, creds, db)


SseUser = Annotated[User, Depends(sse_user_from_request)]
