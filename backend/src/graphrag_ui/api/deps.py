import functools
import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import Project, User
from graphrag_ui.api.errors import ApiError
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.services.auth import resolve_proxy_identity
from graphrag_ui.services.projects import get_member_perms
from graphrag_ui.services.roles import global_perms

_bearer = HTTPBearer(auto_error=False)

# Full set of paths still reachable while must_change_password is true
# (the password-change flow + endpoints that need no login). Checked in
# _principal_from_token, the one gate both bearer paths go through.
MUST_CHANGE_ALLOWED_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/change-password",
        "/api/auth/me",
        "/api/auth/config",
        "/api/health",
        "/api/ready",
    }
)


@dataclass(frozen=True)
class Principal:
    """Request-scoped identity (spec §6.1): the ORM row plus the union of
    the user's global-role atoms, loaded once per request. The delegating
    properties keep route bodies reading `user.id` / `user.email` /
    `user.is_active` / `user.must_change_password` unchanged; guards read
    `global_perms`.

    Read-only on purpose (frozen, properties without setters): any route
    that WRITES to the user row must go through `principal.user`
    (`auth_routes.change_password` is the one such site).
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


async def _principal(db: AsyncSession, user: User) -> Principal:
    return Principal(user=user, global_perms=await global_perms(db, user.id))


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
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated"
        ) from None
    display = (request.headers.get("X-Forwarded-Preferred-Username") or "").strip()[:100]
    user, perms = await resolve_proxy_identity(db, email, display or email.split("@")[0])
    if not user.is_active:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth_user_disabled", "account disabled")
    return Principal(user=user, global_perms=perms)


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
    return await _principal_from_token(request, db, creds.credentials)


async def _principal_from_token(request: Request, db: AsyncSession, token: str) -> Principal:
    """An access token → Principal: 401 when invalid or expired, 403 while
    the user must change their password (the backend enforces the forced
    change, not just the SPA's modal) unless the path is on the allowlist.
    The SSE ?ticket= path shares the gate (R1-104)."""
    return await _gated_principal(request, db, await resolve_access_user(token, db))


async def _gated_principal(request: Request, db: AsyncSession, user: User | None) -> Principal:
    if user is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "auth_invalid_token", "Invalid or expired token"
        )
    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "auth_must_change_password", "password change required"
        )
    return await _principal(db, user)


# Shared dependency types for endpoint parameters (FastAPI-conventional
# Annotated aliases, so endpoints don't repeat a long Annotated[...] each)
DbSession = Annotated[AsyncSession, Depends(get_db)]
# List paging (decision D1): every paged listing takes the same bounds and
# answers {items, total}. Callers give the defaults (limit 50, offset 0).
PageLimit = Annotated[int, Query(ge=1, le=200)]
PageOffset = Annotated[int, Query(ge=0)]
CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_atom(atom: Atom):
    """Router-level dependency: the caller must hold `atom` globally.
    Keeps the historical `admin_only` error code (spec §7) — only the
    message is reworded toward the permission, away from 'admin'."""

    async def _dep(user: CurrentUser) -> Principal:
        if atom.value not in user.global_perms:
            raise ApiError(
                status.HTTP_403_FORBIDDEN, "admin_only", "requires user management permission"
            )
        return user

    return _dep


ManageUsers = Annotated[Principal, Depends(require_atom(Atom.users_manage))]

# Auth for SSE routes (job logs, query stream): EventSource cannot send an
# Authorization header, so these routes take a `?ticket=` minted by
# POST /api/auth/sse-ticket — signed, bound to the request path, valid for
# a minute (F24-01). The access token itself never rides a URL: the nginx
# error log has no redacting format and records the full request line
# whenever the api is unreachable. A header still works (tests, curl).
_sse_bearer = HTTPBearer(auto_error=False)


async def resolve_sse_ticket(ticket: str, path: str, db: AsyncSession) -> User | None:
    """`?ticket=` → User; invalid, expired, minted for another path, not a
    ticket (an access token) or a disabled user → None."""
    try:
        payload = jwt.decode(ticket, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "sse" or payload.get("path") != path:
        return None
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def sse_user_from_request(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_sse_bearer)],
    db: DbSession,
    ticket: Annotated[str | None, Query()] = None,
) -> Principal:
    """?ticket= with get_current_user semantics (401 invalid/expired, 403
    must-change gate) for SSE-only routes; the Bearer header otherwise."""
    # No tokens exist in proxy mode; the EventSource request carries the
    # oauth2-proxy cookie, so the injected headers are the only credential.
    if get_settings().auth_mode == "proxy":
        return await resolve_proxy_user(request, db)
    if ticket is None:
        return await get_current_user(request, creds, db)
    return await _gated_principal(
        request, db, await resolve_sse_ticket(ticket, request.url.path, db)
    )


SseUser = Annotated[Principal, Depends(sse_user_from_request)]


def forbidden() -> ApiError:
    # 403 message is fixed (spec): never leak the reason
    return ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "forbidden")


async def project_or_404(db: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project_not_found", "project not found")
    return project


@dataclass(frozen=True)
class ProjectAccess:
    """A project the caller may act on, with the member atoms the check
    used (None = not a member) so a route that also reports permissions
    does not query them again."""

    project: Project
    member_perms: frozenset[str] | None


@functools.cache
def require_project_access(atom: Atom, *, sse: bool = False):
    """Path-`pid` dependency: 404 for an unknown project, then 403 unless
    the caller holds `atom` on it (R1-10). `sse=True` resolves the caller
    through the ?ticket= path (SseUser) instead of the Bearer header.
    Cached so each (atom, sse) is one callable, i.e. one FastAPI
    dependency-cache entry per request.

    rid-addressed routes (jobs, test runs, results) do not use this: they
    resolve permission through the row's own project and must 404 rather
    than 403 on an unreadable row."""
    user_dep = sse_user_from_request if sse else get_current_user

    async def _dep(
        pid: uuid.UUID,
        db: DbSession,
        user: Annotated[Principal, Depends(user_dep)],
    ) -> ProjectAccess:
        project = await project_or_404(db, pid)
        member_perms = await get_member_perms(db, pid, user.id)
        if not can(user.global_perms, atom, member_perms):
            raise forbidden()
        return ProjectAccess(project=project, member_perms=member_perms)

    return _dep


@functools.cache
def require_project(atom: Atom, *, sse: bool = False):
    """require_project_access, handing the route just the Project."""

    async def _dep(
        access: Annotated[ProjectAccess, Depends(require_project_access(atom, sse=sse))],
    ) -> Project:
        return access.project

    return _dep


ProjectView = Annotated[Project, Depends(require_project(Atom.project_view))]
ProjectEditContent = Annotated[Project, Depends(require_project(Atom.project_edit_content))]
ProjectRunJobs = Annotated[Project, Depends(require_project(Atom.project_run_jobs))]
ProjectEditSettings = Annotated[Project, Depends(require_project(Atom.project_edit_settings))]
ProjectManage = Annotated[Project, Depends(require_project(Atom.project_manage))]
SseProjectView = Annotated[Project, Depends(require_project(Atom.project_view, sse=True))]
ProjectViewAccess = Annotated[ProjectAccess, Depends(require_project_access(Atom.project_view))]
ProjectManageAccess = Annotated[ProjectAccess, Depends(require_project_access(Atom.project_manage))]
