"""Role catalog endpoints (spec §7).

GET /api/roles is open to every authenticated active user — the member
picker and the admin pages need the catalog, and role names leak nothing
sensitive. /api/admin/roles is the users:manage-gated CRUD surface
(require_atom(Atom.users_manage); the historical admin_only error code is
kept, spec §7). Service errors (role_not_found, role_name_taken,
role_is_system, ...) render through the app-level table in api/errors.py.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    ManageUsers,
    get_current_user,
    require_atom,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import RoleOut
from graphrag_ui.domain.permissions import Atom
from graphrag_ui.services.roles import (
    create_role,
    delete_role,
    get_role,
    list_roles,
    update_role,
    usage_counts,
)


class RoleCreateIn(BaseModel):
    scope: str = Field(pattern="^(global|project)$")
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    permissions: list[str]


class RoleUpdateIn(BaseModel):
    # scope is immutable on purpose (spec §5.3): moving a role between
    # scopes would silently re-scope every existing grant. All three
    # fields are required: the verb is PATCH but the body is a full
    # replacement, so a partial payload cannot silently blank
    # `description` or `permissions`. The AdminRoles form always sends
    # every field.
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    permissions: list[str]


def _name_taken() -> ApiError:
    # A concurrent duplicate slipped past the service's check-then-write;
    # the unique index is the backstop, so it surfaces as the same 409 the
    # service's RoleNameTakenError renders (api/errors.py), not a 500.
    return ApiError(
        status.HTTP_409_CONFLICT, "role_name_taken", "a role with that name already exists"
    )


def register_roles_routes(app):
    # Same conventions as users_routes: routers built inside the function
    # (create_app is called repeatedly in tests), auth on the router itself.
    open_router = APIRouter(prefix="/api/roles", dependencies=[Depends(get_current_user)])

    @open_router.get("", response_model=list[RoleOut])
    async def get_roles(
        db: DbSession,
        user: CurrentUser,
        scope: str | None = Query(default=None, pattern="^(global|project)$"),
    ):
        return [RoleOut.model_validate(r) for r in await list_roles(db, scope)]

    app.include_router(open_router)

    admin_router = APIRouter(
        prefix="/api/admin/roles", dependencies=[Depends(require_atom(Atom.users_manage))]
    )

    @admin_router.get("", response_model=list[RoleOut])
    async def admin_get_roles(db: DbSession):
        roles = await list_roles(db)
        counts = await usage_counts(db)
        out = []
        for r in roles:
            ro = RoleOut.model_validate(r)
            ro.user_count = counts.get(r.id, {}).get("users", 0)
            ro.member_count = counts.get(r.id, {}).get("members", 0)
            out.append(ro)
        return out

    @admin_router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
    async def post_role(body: RoleCreateIn, admin: ManageUsers, db: DbSession):
        try:
            role = await create_role(
                db,
                scope=body.scope,
                name=body.name,
                description=body.description,
                permissions=body.permissions,
                actor_id=admin.id,
            )
        except IntegrityError as e:
            raise _name_taken() from e
        return RoleOut.model_validate(role)

    @admin_router.patch("/{role_id}", response_model=RoleOut)
    async def patch_one(role_id: uuid.UUID, body: RoleUpdateIn, admin: ManageUsers, db: DbSession):
        role = await get_role(db, role_id)
        try:
            role = await update_role(
                db,
                role,
                name=body.name,
                description=body.description,
                permissions=body.permissions,
                actor_id=admin.id,
            )
        except IntegrityError as e:
            raise _name_taken() from e
        return RoleOut.model_validate(role)

    @admin_router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_one(role_id: uuid.UUID, admin: ManageUsers, db: DbSession):
        role = await get_role(db, role_id)
        await delete_role(db, role, actor_id=admin.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(admin_router)
