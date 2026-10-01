"""Settings endpoints: read/write settings.yaml with hash optimistic lock and
version history (task brief 3).

Permissions: write is project:edit_settings, reads are project:view.
The 409 body carries the exact keys
{"detail", "code", "current_content", "current_hash"} — the frontend diff
flow (task 7) depends on them.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    PageLimit,
    PageOffset,
    ProjectEditSettings,
    ProjectView,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import SettingsConflictOut, UuidStr
from graphrag_ui.services.settings import (
    SettingsConflictError,
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
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_hash: str
    saved_by: UuidStr
    created_at: datetime


class VersionPageOut(BaseModel):
    """One page of the settings history (decision D1)."""

    items: list[VersionOut]
    total: int


class VersionDetailOut(VersionOut):
    content: str


def register_settings_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/{pid}/settings", response_model=SettingsOut)
    async def get_project_settings(project: ProjectView):
        content, content_hash = read_settings(project)
        return SettingsOut(content=content, content_hash=content_hash)

    @router.put(
        "/{pid}/settings",
        response_model=SettingsWriteOut,
        responses={409: {"model": SettingsConflictOut}},
    )
    async def save_project_settings(
        project: ProjectEditSettings, body: SettingsWriteIn, db: DbSession, user: CurrentUser
    ):
        try:
            new_hash = await write_settings(db, project, body.content, body.expected_hash, user.id)
        except SettingsConflictError as e:
            # The one route-specific shape (400/409 service errors render
            # through the app-level table). Flat JSON body — nesting under
            # {"detail": {...}} would break the frontend's expected keys
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=SettingsConflictOut(
                    detail="conflict",
                    code="settings_conflict",
                    current_content=e.current_content,
                    current_hash=e.current_hash,
                ).model_dump(),
            )
        return SettingsWriteOut(content_hash=new_hash)

    @router.get("/{pid}/settings/versions", response_model=VersionPageOut)
    async def list_settings_versions(
        project: ProjectView, db: DbSession, limit: PageLimit = 50, offset: PageOffset = 0
    ):
        """Newest first, paged (at most 200 per page)."""
        versions, total = await list_versions(db, project, limit=limit, offset=offset)
        return VersionPageOut(
            items=[VersionOut.model_validate(v) for v in versions],
            total=total,
        )

    @router.get("/{pid}/settings/versions/{vid}", response_model=VersionDetailOut)
    async def get_settings_version(project: ProjectView, vid: int, db: DbSession):
        v = await get_version(db, project, vid)
        if v is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "version_not_found", "version not found")
        return VersionDetailOut.model_validate(v)

    app.include_router(router)
