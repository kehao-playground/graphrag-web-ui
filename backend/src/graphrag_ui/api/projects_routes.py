import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, ProjectMember, Role, User
from graphrag_ui.adapters.workspace import (
    GraphragInitInitializer,
    WorkspaceInitError,
    WorkspaceInitializer,
)
from graphrag_ui.api.deps import CurrentUser, DbSession, Principal, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.domain.permissions import Atom, can, effective_project_perms
from graphrag_ui.services.projects import (
    MemberOwnerProtectedError,
    create_project,
    delete_project,
    get_member_perms,
    list_projects,
    member_perms_for_projects,
    remove_member,
    set_member,
    update_project,
)
from graphrag_ui.services.roles import RoleNotFound, RoleScopeMismatchError


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    input_file_type: Literal["text", "csv", "json"]


class ProjectUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    description: str | None
    input_file_type: str
    owner_id: str
    created_at: datetime
    my_permissions: list[str] = []

    @field_validator("id", "owner_id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        # pydantic 2 does not implicitly coerce UUID to str; Project.id / owner_id are UUIDs
        return str(v) if isinstance(v, uuid.UUID) else v


class MemberIn(BaseModel):
    # Single-owner policy: owner is fixed to the creator and not grantable via API.
    role_id: uuid.UUID


class MemberOut(BaseModel):
    user_id: str
    email: EmailStr
    display_name: str
    role_id: str
    role_name: str


def get_initializer() -> WorkspaceInitializer:
    return GraphragInitInitializer()


def _forbidden() -> HTTPException:
    # 403 message is fixed (spec): never leak the reason
    return ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "forbidden")


async def _project_or_404(db: AsyncSession, project_id: uuid.UUID) -> Project:
    # module-level so files_routes (and later task routers) can share the
    # same lookup — never duplicate the query per router
    project = await db.get(Project, project_id)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project_not_found", "project not found")
    return project


def register_projects_routes(app):
    # Router built inside the function (like users_routes): create_app() is called repeatedly in tests
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    async def _require(db: AsyncSession, project: Project, user: Principal, action: Atom) -> None:
        perms = await get_member_perms(db, project.id, user.id)
        if not can(user.global_perms, user.is_active, action, perms):
            raise _forbidden()

    @router.get("", response_model=list[ProjectOut])
    async def list_all(db: DbSession, user: CurrentUser):
        projects = await list_projects(db, user.user, user.global_perms)
        perms = await member_perms_for_projects(db, user.id, [p.id for p in projects])
        out = []
        for p in projects:
            po = ProjectOut.model_validate(p)
            po.my_permissions = sorted(effective_project_perms(user.global_perms, perms.get(p.id)))
            out.append(po)
        return out

    @router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
    async def post_project(
        body: ProjectIn,
        db: DbSession,
        user: CurrentUser,
        initializer: Annotated[WorkspaceInitializer, Depends(get_initializer)],
    ):
        try:
            project = await create_project(
                db,
                body.name,
                body.description,
                body.input_file_type,
                user.user,
                user.global_perms,
                initializer,
            )
        except PermissionError:
            raise _forbidden() from None
        except WorkspaceInitError:
            # The service only raises WorkspaceInitError; HTTP conversion belongs to the route layer
            raise ApiError(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "init_failed", "graphrag init failed"
            ) from None
        return ProjectOut.model_validate(project)

    @router.get("/{project_id}", response_model=ProjectOut)
    async def get_one(project_id: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_view)
        member_perms = await get_member_perms(db, project.id, user.id)
        po = ProjectOut.model_validate(project)
        po.my_permissions = sorted(effective_project_perms(user.global_perms, member_perms))
        return po

    @router.patch("/{project_id}", response_model=ProjectOut)
    async def patch_one(
        project_id: uuid.UUID, body: ProjectUpdateIn, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_manage)
        project = await update_project(
            db, project, name=body.name, description=body.description, actor_id=user.id
        )
        return ProjectOut.model_validate(project)

    @router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_one(project_id: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_manage)
        await delete_project(db, project, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{project_id}/members", response_model=list[MemberOut])
    async def members(project_id: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_view)
        rows = (
            await db.execute(
                select(ProjectMember.user_id, User.email, User.display_name, Role.id, Role.name)
                .join(User, User.id == ProjectMember.user_id)
                .join(Role, Role.id == ProjectMember.role_id)
                .where(ProjectMember.project_id == project.id)
                .order_by(User.email)
            )
        ).all()
        return [
            MemberOut(
                user_id=str(r[0]), email=r[1], display_name=r[2], role_id=str(r[3]), role_name=r[4]
            )
            for r in rows
        ]

    @router.put("/{project_id}/members/{user_id}", response_model=MemberOut)
    async def put_member(
        project_id: uuid.UUID, user_id: uuid.UUID, body: MemberIn, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_manage)
        target = await db.get(User, user_id)
        if target is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found", "user not found")
        try:
            member = await set_member(db, project, user_id, body.role_id, actor_id=user.id)
        except MemberOwnerProtectedError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "member_owner_protected", str(e)) from None
        except RoleNotFound:
            raise ApiError(status.HTTP_404_NOT_FOUND, "role_not_found", "role not found") from None
        except RoleScopeMismatchError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "role_scope_mismatch", str(e)) from None
        role = await db.get(Role, member.role_id)
        # set_member_role validated and assigned this role id in the same
        # transaction, so the row is there; an assert says why rather than
        # letting a later attribute access raise a bare AttributeError.
        assert role is not None, "member.role_id was just set from a loaded role"
        return MemberOut(
            user_id=str(user_id),
            email=target.email,
            display_name=target.display_name,
            role_id=str(role.id),
            role_name=role.name,
        )

    @router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_member(
        project_id: uuid.UUID, user_id: uuid.UUID, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Atom.project_manage)
        try:
            await remove_member(db, project, user_id, actor_id=user.id)
        except MemberOwnerProtectedError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "member_owner_protected", str(e)) from None
        except LookupError:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "member_not_found", "member not found"
            ) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
