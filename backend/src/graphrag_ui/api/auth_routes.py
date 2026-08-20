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

# login 速率限制:記憶體滑動視窗,key = (ip, email 小寫),只計**失敗**嘗試 —
# 成功登入不佔桶(否則團隊尖峰會誤觸 429),桶也以 email 區分,
# 攻擊者灌爆單一桶不影響其他人(模組級 = 單一 worker 內共享)。
_LOGIN_FAILURES: dict[tuple[str, str], deque[datetime]] = {}
_LOGIN_WINDOW = timedelta(minutes=1)
_LOGIN_MAX_ATTEMPTS = 10


def _login_rate_key(request: Request, email: str) -> tuple[str, str]:
    # 部署拓撲中 api 一律在 web nginx 後面(nginx 轉發 X-Forwarded-For、
    # uvicorn 開 --proxy-headers),request.client.host 才會是真實客戶端 IP,
    # 而非整個團隊共享的 web 容器 IP
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
    # router 建在函式內(同 health_routes):create_app() 在測試會被重複呼叫
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
        # 改密後撤銷全部 refresh(含本次登入);commit 會一併寫入上面的 user 變更
        await revoke_all_for_user(db, user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/me", response_model=UserOut)
    async def me(user: CurrentUser):
        return UserOut.model_validate(user)

    app.include_router(router)
