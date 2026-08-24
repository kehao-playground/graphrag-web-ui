import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from graphrag_ui.api.deps import AdminUser, DbSession, get_current_user, require_admin
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import UserBriefOut, UserOut
from graphrag_ui.services.users import (
    LastActiveAdminError,
    SelfRoleChangeError,
    UserNotFound,
    create_user,
    get_user,
    list_users_by_email,
    list_users_ordered,
    patch_user_guarded,
    reset_password,
)


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


def register_users_routes(app):
    # Router built inside the function (like auth_routes): create_app() is called repeatedly in tests
    router = APIRouter(prefix="/api/admin/users", dependencies=[Depends(require_admin)])

    @router.get("", response_model=list[UserOut])
    async def list_users(db: DbSession):
        return [UserOut.model_validate(u) for u in await list_users_ordered(db)]

    @router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
    async def post_user(body: UserCreateIn,
                        admin: AdminUser,
                        db: DbSession):
        try:
            user = await create_user(db, body.email, body.display_name,
                                     body.password, actor_id=admin.id)
        except IntegrityError:
            raise ApiError(status.HTTP_409_CONFLICT,
                           "email_registered", "email already registered") from None
        return UserOut.model_validate(user)

    @router.patch("/{user_id}", response_model=UserOut)
    async def patch_user(user_id: uuid.UUID, body: UserUpdateIn,
                         admin: AdminUser,
                         db: DbSession):
        try:
            user = await patch_user_guarded(db, admin, user_id,
                                            display_name=body.display_name,
                                            role=body.role,
                                            is_active=body.is_active)
        except UserNotFound:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found",
                           "user not found") from None
        except SelfRoleChangeError:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "user_self_change_forbidden",
                           "cannot change your own role or active status") from None
        except LastActiveAdminError:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "user_last_admin_protected",
                           "cannot demote or deactivate the last active admin") from None
        return UserOut.model_validate(user)

    @router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
    async def post_reset_password(user_id: uuid.UUID, body: ResetPasswordIn,
                                  admin: AdminUser,
                                  db: DbSession):
        try:
            user = await get_user(db, user_id)
        except UserNotFound:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found",
                           "user not found") from None
        await reset_password(db, user, body.new_password, actor_id=admin.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)

    # Narrow list available to every logged-in active user (spec §5: member
    # management is the project owner's privilege, so a non-admin owner also
    # needs to resolve email → user_id to add members; admin fields stay private)
    open_router = APIRouter(prefix="/api/users", dependencies=[Depends(get_current_user)])

    @open_router.get("", response_model=list[UserBriefOut])
    async def list_users_brief(db: DbSession):
        return [UserBriefOut.model_validate(u) for u in await list_users_by_email(db)]

    app.include_router(open_router)
