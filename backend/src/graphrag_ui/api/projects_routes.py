import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select

from graphrag_ui.adapters.models import Project, ProjectMember, Role, User
from graphrag_ui.adapters.workspace import GraphragInitInitializer, WorkspaceInitializer
from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    ProjectManage,
    ProjectManageAccess,
    ProjectView,
    ProjectViewAccess,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import UuidStr
from graphrag_ui.domain.permissions import effective_project_perms
from graphrag_ui.services.projects import (
    create_project,
    delete_project,
    get_member_perms,
    list_projects,
    member_perms_for_projects,
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

    id: UuidStr
    name: str
    slug: str
    description: str | None
    input_file_type: str
    owner_id: UuidStr
    created_at: datetime
    my_permissions: list[str] = []


class MemberIn(BaseModel):
    # Single-owner policy: owner is fixed to the creator and not grantable via API.
    role_id: uuid.UUID


class MemberOut(BaseModel):
    user_id: str
    email: EmailStr
    display_name: str
    role_id: str
    role_name: str


def _project_out(
    project: Project, global_perms: frozenset[str], member_perms: frozenset[str] | None
) -> ProjectOut:
    """ProjectOut with the caller's effective atoms: every route that
    answers a project answers them, so no response underreports what the
    caller may do (R3-22)."""
    po = ProjectOut.model_validate(project)
    po.my_permissions = sorted(effective_project_perms(global_perms, member_perms))
    return po


def get_initializer() -> WorkspaceInitializer:
    return GraphragInitInitializer()


def register_projects_routes(app):
    # Router built inside the function (like users_routes): create_app() is called repeatedly in tests
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("", response_model=list[ProjectOut])
    async def list_all(db: DbSession, user: CurrentUser):
        projects = await list_projects(db, user.user, user.global_perms)
        perms = await member_perms_for_projects(db, user.id, [p.id for p in projects])
        return [_project_out(p, user.global_perms, perms.get(p.id)) for p in projects]

    @router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
    async def post_project(
        body: ProjectIn,
        db: DbSession,
        user: CurrentUser,
        initializer: Annotated[WorkspaceInitializer, Depends(get_initializer)],
    ):
        project = await create_project(
            db, body.name, body.description, body.input_file_type, user.user, initializer
        )
        # the owner membership create_project just wrote
        member_perms = await get_member_perms(db, project.id, user.id)
        return _project_out(project, user.global_perms, member_perms)

    @router.get("/{pid}", response_model=ProjectOut)
    async def get_one(access: ProjectViewAccess, user: CurrentUser):
        return _project_out(access.project, user.global_perms, access.member_perms)

    @router.patch("/{pid}", response_model=ProjectOut)
    async def patch_one(
        body: ProjectUpdateIn, access: ProjectManageAccess, db: DbSession, user: CurrentUser
    ):
        project = await update_project(
            db, access.project, name=body.name, description=body.description, actor_id=user.id
        )
        return _project_out(project, user.global_perms, access.member_perms)

    @router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_one(project: ProjectManage, db: DbSession, user: CurrentUser):
        await delete_project(db, project, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/members", response_model=list[MemberOut])
    async def members(project: ProjectView, db: DbSession):
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

    @router.put("/{pid}/members/{user_id}", response_model=MemberOut)
    async def put_member(
        user_id: uuid.UUID, body: MemberIn, project: ProjectManage, db: DbSession, user: CurrentUser
    ):
        target = await db.get(User, user_id)
        if target is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found", "user not found")
        _, role = await set_member(db, project, user_id, body.role_id, actor_id=user.id)
        return MemberOut(
            user_id=str(user_id),
            email=target.email,
            display_name=target.display_name,
            role_id=str(role.id),
            role_name=role.name,
        )

    @router.delete("/{pid}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_member(
        user_id: uuid.UUID, project: ProjectManage, db: DbSession, user: CurrentUser
    ):
        await remove_member(db, project, user_id, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
