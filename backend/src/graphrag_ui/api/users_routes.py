import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from graphrag_ui.api.deps import (
    DbSession,
    ManageUsers,
    get_current_user,
    require_atom,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import UserBriefOut, UserOut, user_out
from graphrag_ui.domain.permissions import Atom
from graphrag_ui.services import users as users_service
from graphrag_ui.services.roles import list_roles_for_user

# adapters.models.User.display_name is String(100): longer is a 422 here,
# not a truncation error at flush.
DisplayName = Annotated[str, Field(min_length=1, max_length=100)]


class UserCreateIn(BaseModel):
    email: EmailStr
    display_name: DisplayName
    password: str = Field(min_length=8)
    roles: list[uuid.UUID] = []


class UserUpdateIn(BaseModel):
    display_name: DisplayName | None = None
    roles: list[uuid.UUID] | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8)


def register_users_routes(app):
    # Router built inside the function (like auth_routes): create_app() is called repeatedly in tests
    router = APIRouter(
        prefix="/api/admin/users", dependencies=[Depends(require_atom(Atom.users_manage))]
    )

    @router.get("", response_model=list[UserOut])
    async def list_users(db: DbSession):
        return [user_out(u, roles) for u, roles in await users_service.list_users_with_roles(db)]

    @router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
    async def create_user(body: UserCreateIn, admin: ManageUsers, db: DbSession):
        try:
            user = await users_service.create_user(
                db,
                body.email,
                body.display_name,
                body.password,
                role_ids=body.roles,
                actor_id=admin.id,
            )
        except IntegrityError:
            raise ApiError(
                status.HTTP_409_CONFLICT, "email_registered", "email already registered"
            ) from None
        return user_out(user, await list_roles_for_user(db, user.id))

    @router.patch("/{user_id}", response_model=UserOut)
    async def update_user(
        user_id: uuid.UUID, body: UserUpdateIn, admin: ManageUsers, db: DbSession
    ):
        user = await users_service.patch_user_guarded(
            db,
            admin.id,
            admin.global_perms,
            user_id,
            display_name=body.display_name,
            role_ids=body.roles,
            is_active=body.is_active,
        )
        return user_out(user, await list_roles_for_user(db, user.id))

    @router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_user_password(
        user_id: uuid.UUID, body: ResetPasswordIn, admin: ManageUsers, db: DbSession
    ):
        user = await users_service.get_user(db, user_id)
        await users_service.reset_password(db, user, body.new_password, actor_id=admin.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)

    # Narrow list available to every logged-in active user (spec §5: member
    # management is the project owner's privilege, so a non-admin owner also
    # needs to resolve email → user_id to add members; admin fields stay private)
    open_router = APIRouter(prefix="/api/users", dependencies=[Depends(get_current_user)])

    @open_router.get("", response_model=list[UserBriefOut])
    async def list_users_brief(db: DbSession):
        return [UserBriefOut.model_validate(u) for u in await users_service.list_users_by_email(db)]

    app.include_router(open_router)
