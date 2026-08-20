import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import User
from graphrag_ui.api.deps import get_current_user, get_db
from graphrag_ui.api.schemas import UserBriefOut, UserOut
from graphrag_ui.services.users import create_user, reset_password, update_user


class UserCreateIn(BaseModel):
    email: EmailStr
    display_name: str
    password: str = Field(min_length=8)


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8)


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


async def _other_active_admin_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (await session.execute(
        select(func.count()).select_from(User).where(
            User.role == "admin", User.is_active.is_(True), User.id != user_id)
    )).scalar_one()


def register_users_routes(app):
    # router 建在函式內(同 auth_routes):create_app() 在測試會被重複呼叫
    router = APIRouter(prefix="/api/admin/users", dependencies=[Depends(require_admin)])

    @router.get("", response_model=list[UserOut])
    async def list_users(db: Annotated[AsyncSession, Depends(get_db)]):
        # 明確排序 — 測試與前端不該依賴 DB 的隱含回傳順序
        users = (await db.execute(
            select(User).order_by(User.created_at, User.id))).scalars().all()
        return [UserOut.model_validate(u) for u in users]

    @router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
    async def post_user(body: UserCreateIn,
                        admin: Annotated[User, Depends(require_admin)],
                        db: Annotated[AsyncSession, Depends(get_db)]):
        try:
            user = await create_user(db, body.email, body.display_name,
                                     body.password, actor_id=admin.id)
        except IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None
        return UserOut.model_validate(user)

    @router.patch("/{user_id}", response_model=UserOut)
    async def patch_user(user_id: uuid.UUID, body: UserUpdateIn,
                         admin: Annotated[User, Depends(require_admin)],
                         db: Annotated[AsyncSession, Depends(get_db)]):
        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        if user.id == admin.id and (body.role is not None or body.is_active is not None):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "cannot change your own role or active status")
        demotes = body.role is not None and body.role != "admin"
        # 不能把最後一個 active admin 降級或停用,否則系統會被鎖死
        if (user.role == "admin" and user.is_active and (demotes or body.is_active is False)
                and await _other_active_admin_count(db, user.id) == 0):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "cannot demote or deactivate the last active admin")
        user = await update_user(db, user, display_name=body.display_name, role=body.role,
                                 is_active=body.is_active, actor_id=admin.id)
        return UserOut.model_validate(user)

    @router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
    async def post_reset_password(user_id: uuid.UUID, body: ResetPasswordIn,
                                  admin: Annotated[User, Depends(require_admin)],
                                  db: Annotated[AsyncSession, Depends(get_db)]):
        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        await reset_password(db, user, body.new_password, actor_id=admin.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)

    # 所有已登入 active 使用者可用的窄清單(spec §5:成員管理是專案 owner 的權限,
    # 非 admin owner 也需要能解析 email → user_id 來加成員;管理欄位不外洩)
    open_router = APIRouter(prefix="/api/users", dependencies=[Depends(get_current_user)])

    @open_router.get("", response_model=list[UserBriefOut])
    async def list_users_brief(db: Annotated[AsyncSession, Depends(get_db)]):
        users = (await db.execute(select(User).order_by(User.email))).scalars().all()
        return [UserBriefOut.model_validate(u) for u in users]

    app.include_router(open_router)
