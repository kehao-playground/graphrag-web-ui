from collections import deque
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status

from graphrag_ui.adapters.models import User
from graphrag_ui.api.deps import CurrentUser, DbSession
from graphrag_ui.api.schemas import (
    ChangePasswordIn,
    LoginIn,
    LoginOut,
    RefreshIn,
    RefreshOut,
    UserOut,
)
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

# Login rate limiting: in-memory sliding window keyed by (ip, lowercased
# email), counting only **failed** attempts — successful logins never fill a
# bucket (a team spike must not trip 429), and per-email buckets keep one
# attacker flooding a single bucket from affecting others (module-level =
# shared within a single worker).
_LOGIN_FAILURES: dict[tuple[str, str], deque[datetime]] = {}
_LOGIN_WINDOW = timedelta(minutes=1)
_LOGIN_MAX_ATTEMPTS = 10


def _login_rate_key(request: Request, email: str) -> tuple[str, str]:
    # In the deployment topology the api always sits behind the web nginx
    # (nginx forwards X-Forwarded-For, uvicorn runs --proxy-headers), so
    # request.client.host is the real client IP rather than the web
    # container IP shared by the whole team
    ip = request.client.host if request.client else "unknown"
    return (ip, email.lower())


def _check_login_rate_limit(request: Request, email: str) -> None:
    attempts = _LOGIN_FAILURES.get(_login_rate_key(request, email))
    if not attempts:
        return
    now = datetime.now(UTC)
    while attempts and now - attempts[0] > _LOGIN_WINDOW:
        attempts.popleft()
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts")


def _record_login_failure(request: Request, email: str) -> None:
    _LOGIN_FAILURES.setdefault(_login_rate_key(request, email), deque()).append(
        datetime.now(UTC))


def register_auth_routes(app):
    # Router built inside the function (like health_routes): create_app() is
    # called repeatedly in tests
    router = APIRouter(prefix="/api/auth")

    @router.post("/login", response_model=LoginOut)
    async def login(body: LoginIn, request: Request, db: DbSession):
        _check_login_rate_limit(request, body.email)
        user = await authenticate(db, body.email, body.password)
        if user is None:
            _record_login_failure(request, body.email)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        return LoginOut(
            access_token=create_access_token(user),
            refresh_token=await issue_refresh_token(db, user.id),
            user=UserOut.model_validate(user),
        )

    @router.post("/refresh", response_model=RefreshOut)
    async def refresh(body: RefreshIn, db: DbSession):
        rotated = await rotate_refresh(db, body.refresh_token)
        if rotated is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
        user_id, new_refresh = rotated
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
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
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "incorrect current password")
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False
        # Changing the password revokes every refresh token (including this
        # login's); the commit also flushes the user mutation above
        await revoke_all_for_user(db, user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/me", response_model=UserOut)
    async def me(user: CurrentUser):
        return UserOut.model_validate(user)

    app.include_router(router)
