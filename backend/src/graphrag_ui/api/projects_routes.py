import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, User
from graphrag_ui.adapters.workspace import GraphragInitInitializer, WorkspaceInitializer
from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    Principal,
    ProjectManage,
    ProjectManageAccess,
    ProjectView,
    ProjectViewAccess,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import UuidStr
from graphrag_ui.domain.permissions import effective_project_perms
from graphrag_ui.services import projects as projects_service


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    input_file_type: Literal["text", "csv", "json"]


class ProjectUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(BaseModel):
    id: UuidStr
    name: str
    slug: str
    description: str | None
    input_file_type: str
    owner_id: UuidStr
    # The owner's identity rides along so the project list needs no users
    # fetch to render it (R1-117).
    owner_email: str
    owner_display_name: str
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
    project: Project,
    owner: User,
    global_perms: frozenset[str],
    member_perms: frozenset[str] | None,
) -> ProjectOut:
    """ProjectOut with the owner's identity and the caller's effective
    atoms: every route that answers a project answers them, so no response
    underreports what the caller may do (R3-22)."""
    return ProjectOut(
        id=str(project.id),
        name=project.name,
        slug=project.slug,
        description=project.description,
        input_file_type=project.input_file_type,
        owner_id=str(project.owner_id),
        owner_email=owner.email,
        owner_display_name=owner.display_name,
        created_at=project.created_at,
        my_permissions=sorted(effective_project_perms(global_perms, member_perms)),
    )


async def _one_project_out(
    db: AsyncSession, project: Project, user: Principal, member_perms: frozenset[str] | None
) -> ProjectOut:
    # owner_id is a non-null FK: the row exists (and is the identity-map
    # hit when the caller owns the project).
    owner = await db.get(User, project.owner_id)
    assert owner is not None
    return _project_out(project, owner, user.global_perms, member_perms)


def get_initializer() -> WorkspaceInitializer:
    return GraphragInitInitializer()


def register_projects_routes(app):
    # Router built inside the function (like users_routes): create_app() is called repeatedly in tests
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("", response_model=list[ProjectOut])
    async def list_projects(db: DbSession, user: CurrentUser):
        projects = await projects_service.list_projects(db, user.user, user.global_perms)
        perms = await projects_service.member_perms_for_projects(
            db, user.id, [p.id for p in projects]
        )
        owners = await projects_service.owners_of(db, projects)
        return [
            _project_out(p, owners[p.owner_id], user.global_perms, perms.get(p.id))
            for p in projects
        ]

    @router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
    async def create_project(
        body: ProjectIn,
        db: DbSession,
        user: CurrentUser,
        initializer: Annotated[WorkspaceInitializer, Depends(get_initializer)],
    ):
        project, owner_perms = await projects_service.create_project(
            db, body.name, body.description, body.input_file_type, user.user, initializer
        )
        return await _one_project_out(db, project, user, owner_perms)

    @router.get("/{pid}", response_model=ProjectOut)
    async def get_project(access: ProjectViewAccess, db: DbSession, user: CurrentUser):
        return await _one_project_out(db, access.project, user, access.member_perms)

    @router.patch("/{pid}", response_model=ProjectOut)
    async def update_project(
        body: ProjectUpdateIn, access: ProjectManageAccess, db: DbSession, user: CurrentUser
    ):
        project = await projects_service.update_project(
            db, access.project, name=body.name, description=body.description, actor_id=user.id
        )
        return await _one_project_out(db, project, user, access.member_perms)

    @router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project: ProjectManage, db: DbSession, user: CurrentUser):
        await projects_service.delete_project(db, project, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/members", response_model=list[MemberOut])
    async def list_members(project: ProjectView, db: DbSession):
        return [
            MemberOut(
                user_id=str(m.user_id),
                email=m.email,
                display_name=m.display_name,
                role_id=str(m.role_id),
                role_name=m.role_name,
            )
            for m in await projects_service.list_members(db, project.id)
        ]

    @router.put("/{pid}/members/{user_id}", response_model=MemberOut)
    async def set_member(
        user_id: uuid.UUID, body: MemberIn, project: ProjectManage, db: DbSession, user: CurrentUser
    ):
        target = await db.get(User, user_id)
        if target is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "user_not_found", "user not found")
        _, role = await projects_service.set_member(
            db, project, user_id, body.role_id, actor_id=user.id
        )
        return MemberOut(
            user_id=str(user_id),
            email=target.email,
            display_name=target.display_name,
            role_id=str(role.id),
            role_name=role.name,
        )

    @router.delete("/{pid}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_member(
        user_id: uuid.UUID, project: ProjectManage, db: DbSession, user: CurrentUser
    ):
        await projects_service.remove_member(db, project, user_id, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
