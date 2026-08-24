import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, ProjectMember, User
from graphrag_ui.adapters.workspace import (
    GraphragInitInitializer,
    WorkspaceInitError,
    WorkspaceInitializer,
)
from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.projects import (
    MemberOwnerProtectedError,
    create_project,
    delete_project,
    get_project_role,
    list_projects,
    remove_member,
    set_member,
    update_project,
)


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

    @field_validator("id", "owner_id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        # pydantic 2 does not implicitly coerce UUID to str; Project.id / owner_id are UUIDs
        return str(v) if isinstance(v, uuid.UUID) else v


class MemberIn(BaseModel):
    # Single-owner policy: owner is fixed to the creator and not grantable via API.
    role: Literal["editor", "viewer"]


class MemberOut(BaseModel):
    user_id: str
    email: EmailStr
    display_name: str
    role: str


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

    async def _require(db: AsyncSession, project: Project, user: User,
                       action: Action) -> None:
        role = await get_project_role(db, project.id, user.id)
        if not can(user.role, user.is_active, action, role):
            raise _forbidden()

    @router.get("", response_model=list[ProjectOut])
    async def list_all(db: DbSession,
                       user: CurrentUser):
        return [ProjectOut.model_validate(p) for p in await list_projects(db, user)]

    @router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
    async def post_project(body: ProjectIn,
                           db: DbSession,
                           user: CurrentUser,
                           initializer: Annotated[WorkspaceInitializer,
                                                  Depends(get_initializer)]):
        try:
            project = await create_project(db, body.name, body.description,
                                           body.input_file_type, user, initializer)
        except PermissionError:
            raise _forbidden() from None
        except WorkspaceInitError:
            # The service only raises WorkspaceInitError; HTTP conversion belongs to the route layer
            raise ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR,
                           "init_failed", "graphrag init failed") from None
        return ProjectOut.model_validate(project)

    @router.get("/{project_id}", response_model=ProjectOut)
    async def get_one(project_id: uuid.UUID,
                      db: DbSession,
                      user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.view_project)
        return ProjectOut.model_validate(project)

    @router.patch("/{project_id}", response_model=ProjectOut)
    async def patch_one(project_id: uuid.UUID, body: ProjectUpdateIn,
                        db: DbSession,
                        user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.update_project)
        project = await update_project(db, project, name=body.name,
                                       description=body.description, actor_id=user.id)
        return ProjectOut.model_validate(project)

    @router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_one(project_id: uuid.UUID,
                         db: DbSession,
                         user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.delete_project)
        await delete_project(db, project, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{project_id}/members", response_model=list[MemberOut])
    async def list_members(project_id: uuid.UUID,
                           db: DbSession,
                           user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.view_project)
        # join users to pull email/display_name; order explicitly, never rely on implicit DB order
        rows = (await db.execute(
            select(ProjectMember.user_id, ProjectMember.role, User.email, User.display_name)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
            .order_by(User.email))).all()
        return [MemberOut(user_id=str(r.user_id), email=r.email,
                          display_name=r.display_name, role=r.role) for r in rows]

    @router.put("/{project_id}/members/{user_id}", response_model=MemberOut)
    async def put_member(project_id: uuid.UUID, user_id: uuid.UUID, body: MemberIn,
                         db: DbSession,
                         user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.manage_members)
        target = await db.get(User, user_id)
        if target is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found", "user not found")
        try:
            await set_member(db, project, user_id, body.role, actor_id=user.id)
        except MemberOwnerProtectedError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "member_owner_protected",
                           str(e)) from None
        return MemberOut(user_id=str(user_id), email=target.email,
                         display_name=target.display_name, role=body.role)

    @router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_member(project_id: uuid.UUID, user_id: uuid.UUID,
                            db: DbSession,
                            user: CurrentUser):
        project = await _project_or_404(db, project_id)
        await _require(db, project, user, Action.manage_members)
        try:
            await remove_member(db, project, user_id, actor_id=user.id)
        except MemberOwnerProtectedError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "member_owner_protected",
                           str(e)) from None
        except LookupError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "member_not_found",
                           "member not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
