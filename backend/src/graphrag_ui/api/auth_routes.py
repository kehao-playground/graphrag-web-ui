from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import User
from graphrag_ui.api.deps import get_current_user, get_db
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

# login per-IP 速率限制:記憶體滑動視窗,>10 次/分 → 429(模組級 = 單一 worker 內共享)
_LOGIN_ATTEMPTS: dict[str, deque[datetime]] = {}
_LOGIN_WINDOW = timedelta(minutes=1)
_LOGIN_MAX_ATTEMPTS = 10


def _enforce_login_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(UTC)
    attempts = _LOGIN_ATTEMPTS.setdefault(ip, deque())
    while attempts and now - attempts[0] > _LOGIN_WINDOW:
        attempts.popleft()
    attempts.append(now)  # 成功與失敗都計數
    if len(attempts) > _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts")


def register_auth_routes(app):
    # router 建在函式內(同 health_routes):create_app() 在測試會被重複呼叫
    router = APIRouter(prefix="/api/auth")

    @router.post("/login", response_model=LoginOut)
    async def login(body: LoginIn, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
        _enforce_login_rate_limit(request)
        user = await authenticate(db, body.email, body.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        return LoginOut(
            access_token=create_access_token(user),
            refresh_token=await issue_refresh_token(db, user.id),
            user=UserOut.model_validate(user),
        )

    @router.post("/refresh", response_model=RefreshOut)
    async def refresh(body: RefreshIn, db: Annotated[AsyncSession, Depends(get_db)]):
        rotated = await rotate_refresh(db, body.refresh_token)
        if rotated is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
        user_id, new_refresh = rotated
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
        return RefreshOut(access_token=create_access_token(user), refresh_token=new_refresh)

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(body: RefreshIn, db: Annotated[AsyncSession, Depends(get_db)]):
        await revoke_refresh(db, body.refresh_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
    async def change_password(
        body: ChangePasswordIn,
        user: Annotated[User, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ):
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "incorrect current password")
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False
        # 改密後撤銷全部 refresh(含本次登入);commit 會一併寫入上面的 user 變更
        await revoke_all_for_user(db, user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/me", response_model=UserOut)
    async def me(user: Annotated[User, Depends(get_current_user)]):
        return UserOut.model_validate(user)

    app.include_router(router)
