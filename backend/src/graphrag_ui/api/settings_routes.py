"""Settings endpoints: read/write settings.yaml with hash optimistic lock and
version history (task brief 3).

Permissions: write is editor+ (Action.edit_content), reads are viewer+
(Action.view_project). The 409 body carries the exact keys
{"detail", "code", "current_content", "current_hash"} — the frontend diff
flow (task 7) depends on them.
"""
import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.projects import get_project_role
from graphrag_ui.services.settings import (
    SettingsConflictError,
    SettingsValidationError,
    get_version,
    list_versions,
    read_settings,
    write_settings,
)


class SettingsOut(BaseModel):
    content: str
    content_hash: str


class SettingsWriteIn(BaseModel):
    content: str
    expected_hash: str


class SettingsWriteOut(BaseModel):
    content_hash: str


class VersionOut(BaseModel):
    id: int
    content_hash: str
    saved_by: str
    created_at: str


class VersionDetailOut(VersionOut):
    content: str


def register_settings_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/{pid}/settings", response_model=SettingsOut)
    async def get_settings(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        content, content_hash = read_settings(project)
        return SettingsOut(content=content, content_hash=content_hash)

    @router.put("/{pid}/settings", response_model=SettingsWriteOut)
    async def put_settings(pid: uuid.UUID, body: SettingsWriteIn,
                           db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        try:
            new_hash = await write_settings(db, project, body.content,
                                            body.expected_hash, user.id)
        except SettingsConflictError as e:
            # flat JSON body — nesting under {"detail": {...}} would break the
            # frontend's expected keys
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
                "detail": "conflict",
                "code": "settings_conflict",
                "current_content": e.current_content,
                "current_hash": e.current_hash,
            })
        except SettingsValidationError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e),
                           e.params) from None
        return SettingsWriteOut(content_hash=new_hash)

    @router.get("/{pid}/settings/versions", response_model=list[VersionOut])
    async def list_settings_versions(pid: uuid.UUID, db: DbSession,
                                     user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        return [VersionOut(id=v.id, content_hash=v.content_hash,
                           saved_by=str(v.saved_by),
                           created_at=v.created_at.isoformat())
                for v in await list_versions(db, project)]

    @router.get("/{pid}/settings/versions/{vid}", response_model=VersionDetailOut)
    async def get_settings_version(pid: uuid.UUID, vid: int,
                                   db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        v = await get_version(db, project, vid)
        if v is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "version_not_found",
                           "version not found")
        return VersionDetailOut(id=v.id, content=v.content,
                                content_hash=v.content_hash,
                                saved_by=str(v.saved_by),
                                created_at=v.created_at.isoformat())

    app.include_router(router)
