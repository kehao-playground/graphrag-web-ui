import time

from fastapi import APIRouter, Request, Response, status

from graphrag_ui.adapters.models import User
from graphrag_ui.api.deps import CurrentUser, DbSession
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import (
    AuthConfigOut,
    ChangePasswordIn,
    LoginIn,
    LoginOut,
    RefreshIn,
    RefreshOut,
    UserOut,
    user_out,
)
from graphrag_ui.config import get_settings
from graphrag_ui.domain.sliding_window import SlidingWindow
from graphrag_ui.services.auth import (
    authenticate,
    create_access_token,
    hash_password,
    issue_refresh_token,
    revoke_all_for_user,
    revoke_refresh,
    rotate_refresh,
    verify_password,
)
from graphrag_ui.services.roles import roles_for_user

# Login rate limiting: in-memory sliding window keyed by (ip, lowercased
# email), counting only **failed** attempts — successful logins never fill a
# bucket (a team spike must not trip 429), and per-email buckets keep one
# attacker flooding a single bucket from affecting others (module-level =
# shared within a single worker).
#
# Both halves of the key come from the request, so the key space is
# attacker-chosen: unique emails, and — for anyone able to reach the api
# past the reverse proxy — spoofed X-Forwarded-For values. SlidingWindow
# caps the number of live buckets and drops expired ones, so a failed-login
# flood costs bounded memory. Monotonic clock: a wall-clock jump must not
# retire a window early (or freeze one open).
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_MAX_TRACKED_KEYS = 10_000
_LOGIN_FAILURES = SlidingWindow(
    window_seconds=_LOGIN_WINDOW_SECONDS, max_keys=_LOGIN_MAX_TRACKED_KEYS
)

# Module-level clock so tests can drive the window deterministically.
_now = time.monotonic


def _login_rate_key(request: Request, email: str) -> tuple[str, str]:
    # In the deployment topology the api always sits behind the web nginx
    # (nginx forwards X-Forwarded-For, uvicorn runs --proxy-headers with a
    # narrowed --forwarded-allow-ips), so request.client.host is the real
    # client IP rather than the web container IP shared by the whole team
    ip = request.client.host if request.client else "unknown"
    return (ip, email.lower())


def _check_login_rate_limit(request: Request, email: str) -> None:
    if _LOGIN_FAILURES.count(_login_rate_key(request, email), _now()) >= _LOGIN_MAX_ATTEMPTS:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS, "auth_too_many_attempts", "too many attempts"
        )


def _record_login_failure(request: Request, email: str) -> None:
    _LOGIN_FAILURES.add(_login_rate_key(request, email), _now())


def register_auth_routes(app):
    # Router built inside the function (like health_routes): create_app() is
    # called repeatedly in tests
    router = APIRouter(prefix="/api/auth")

    @router.get("/config", response_model=AuthConfigOut)
    async def auth_config():
        """Public mode probe: the SPA's single source of truth (spec §5.3)."""
        return AuthConfigOut(auth_mode=get_settings().auth_mode)

    @router.get("/me", response_model=UserOut)
    async def me(user: CurrentUser, db: DbSession):
        return user_out(user.user, await roles_for_user(db, user.user.id))

    if get_settings().auth_mode == "proxy":
        # Proxy mode replaces the local login surface entirely (spec §5.3):
        # unregistered routes 404. This is also the first get_settings()
        # call create_app() makes, so the §4 secret validator fires here.
        app.include_router(router)
        return

    @router.post("/login", response_model=LoginOut)
    async def login(body: LoginIn, request: Request, db: DbSession):
        _check_login_rate_limit(request, body.email)
        user = await authenticate(db, body.email, body.password)
        if user is None:
            _record_login_failure(request, body.email)
            raise ApiError(
                status.HTTP_401_UNAUTHORIZED,
                "auth_invalid_credentials",
                "invalid email or password",
            )
        return LoginOut(
            access_token=create_access_token(user),
            refresh_token=await issue_refresh_token(db, user.id),
            user=user_out(user, await roles_for_user(db, user.id)),
        )

    @router.post("/refresh", response_model=RefreshOut)
    async def refresh(body: RefreshIn, db: DbSession):
        rotated = await rotate_refresh(db, body.refresh_token)
        if rotated is None:
            raise ApiError(
                status.HTTP_401_UNAUTHORIZED, "auth_invalid_refresh_token", "invalid refresh token"
            )
        user_id, new_refresh = rotated
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise ApiError(
                status.HTTP_401_UNAUTHORIZED, "auth_invalid_refresh_token", "invalid refresh token"
            )
        return RefreshOut(access_token=create_access_token(user), refresh_token=new_refresh)

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(body: RefreshIn, db: DbSession):
        await revoke_refresh(db, body.refresh_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
    async def change_password(
        body: ChangePasswordIn,
        user: CurrentUser,
        db: DbSession,
    ):
        if not verify_password(body.current_password, user.user.password_hash):
            raise ApiError(
                status.HTTP_400_BAD_REQUEST,
                "auth_wrong_current_password",
                "incorrect current password",
            )
        user.user.password_hash = hash_password(body.new_password)
        user.user.must_change_password = False
        # Changing the password revokes every refresh token (including this
        # login's); the commit also flushes the user mutation above
        await revoke_all_for_user(db, user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
