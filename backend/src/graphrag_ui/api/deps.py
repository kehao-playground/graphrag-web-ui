import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import User
from graphrag_ui.config import get_settings

_bearer = HTTPBearer(auto_error=False)

# must_change_password 為真時仍可存取的完整路徑集合(改密碼流程 + 無需登入的端點)。
# 單一來源:main.py 的全域 middleware 與 get_current_user 共用,兩處不得各自漂移。
MUST_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
    "/api/auth/change-password", "/api/auth/me",
    "/api/health", "/api/ready",
})


async def get_db():
    # 每個請求開一個 session;factory 本身是 lazy singleton(adapters/db.py)
    async with get_session_factory()() as session:
        yield session


async def resolve_access_user(token: str, db: AsyncSession) -> User | None:
    """Bearer JWT → User;無效/過期/非 access type/使用者停用 → None。

    token 沒有 `aud` claim,decode 不得傳 `audience=`,否則必定 InvalidAudienceError。
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
    """後續 task 共用的 Bearer 鑑權依賴;失敗一律 401。"""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = await resolve_access_user(creds.credentials, db)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # 後端也要擋強制改密碼,不能只靠前端 Modal
    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED_PATHS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password change required")
    return user
