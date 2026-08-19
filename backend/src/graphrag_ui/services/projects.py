import re
import secrets
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, ProjectMember, User
from graphrag_ui.adapters.workspace import WorkspaceInitError, WorkspaceInitializer
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.audit import audit


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = _slugify(name)
    slug = base
    while (await session.execute(
            select(Project.id).where(Project.slug == slug).limit(1))).first() is not None:
        slug = f"{base}-{secrets.token_hex(3)}"  # 碰撞 → 短隨機後綴
    return slug


def _ws_path(project_id: uuid.UUID) -> Path:
    """workspace 目錄;所有檔案操作前 resolve 並斷言仍在 workspaces root 內(spec §10)。"""
    root = Path(get_settings().workspaces_dir).resolve()
    path = (root / str(project_id)).resolve()
    if not path.is_relative_to(root):
        msg = f"workspace path escapes workspaces dir: {path}"
        raise ValueError(msg)
    return path


async def create_project(session: AsyncSession, name: str, description: str | None,
                         input_file_type: str, creator: User,
                         initializer: WorkspaceInitializer) -> Project:
    if not can(creator.role, creator.is_active, Action.create_project):
        raise PermissionError("forbidden")
    project = Project(
        name=name,
        slug=await _unique_slug(session, name),
        description=description,
        owner_id=creator.id,
        input_file_type=input_file_type,
    )
    session.add(project)
    await session.flush()  # 取得 project.id;成功跑完 init 才 commit
    session.add(ProjectMember(project_id=project.id, user_id=creator.id, role="owner"))
    await audit(session, creator.id, "project.created", "project", str(project.id),
                payload={"name": name, "slug": project.slug,
                         "input_file_type": input_file_type})
    try:
        await initializer.init(_ws_path(project.id), input_file_type)
    except WorkspaceInitError:
        await session.rollback()  # init 失敗不留 half-baked row,原樣往上拋
        raise
    await session.commit()
    return project


async def get_project_role(session: AsyncSession, project_id: uuid.UUID,
                           user_id: uuid.UUID) -> str | None:
    return (await session.execute(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id))).scalar_one_or_none()


async def list_projects(session: AsyncSession, user: User) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at, Project.id)
    if user.role != "admin":  # admin 看全部;一般使用者只看自己是成員的
        stmt = stmt.join(ProjectMember).where(ProjectMember.user_id == user.id)
    return list((await session.execute(stmt)).scalars().all())


async def update_project(session: AsyncSession, project: Project, *, name: str | None = None,
                         description: str | None = None,
                         actor_id: uuid.UUID | None) -> Project:
    changed: dict = {}
    if name is not None and name != project.name:
        project.name = name
        changed["name"] = name
    if description is not None and description != project.description:
        project.description = description
        changed["description"] = description
    if not changed:  # 空 PATCH 不算寫入操作,不寫 audit
        return project
    await audit(session, actor_id, "project.updated", "project", str(project.id),
                payload=changed)
    await session.commit()
    return project


async def delete_project(session: AsyncSession, project: Project,
                         actor_id: uuid.UUID | None) -> None:
    ws = _ws_path(project.id)
    if ws.exists():
        shutil.rmtree(ws)
    await audit(session, actor_id, "project.deleted", "project", str(project.id),
                payload={"name": project.name})
    # 成員列靠 FK ondelete=CASCADE 清掉,service 不手動刪
    await session.delete(project)
    await session.commit()


async def set_member(session: AsyncSession, project: Project, user_id: uuid.UUID,
                     role: str, actor_id: uuid.UUID | None) -> ProjectMember:
    if user_id == project.owner_id and role != "owner":
        msg = "cannot demote or remove the project owner"
        raise ValueError(msg)
    member = await session.get(ProjectMember, {"project_id": project.id, "user_id": user_id})
    if member is None:
        member = ProjectMember(project_id=project.id, user_id=user_id, role=role)
        session.add(member)
        await audit(session, actor_id, "member.added", "project", str(project.id),
                    payload={"user_id": str(user_id), "role": role})
    elif member.role != role:
        member.role = role
        await audit(session, actor_id, "member.role_changed", "project", str(project.id),
                    payload={"user_id": str(user_id), "role": role})
    else:
        return member  # 同角色 = 無變更,不寫 audit
    await session.commit()
    return member


async def remove_member(session: AsyncSession, project: Project, user_id: uuid.UUID,
                        actor_id: uuid.UUID | None) -> None:
    if user_id == project.owner_id:
        msg = "cannot demote or remove the project owner"
        raise ValueError(msg)
    member = await session.get(ProjectMember, {"project_id": project.id, "user_id": user_id})
    if member is None:
        raise LookupError("member not found")
    await session.delete(member)
    await audit(session, actor_id, "member.removed", "project", str(project.id),
                payload={"user_id": str(user_id)})
    await session.commit()
