"""File endpoints: upload/list/delete project input files (spec §6.3).

Permissions: upload/delete are editor+ (Action.edit_content), listing is
viewer+ (Action.view_project). Audit actions: file.uploaded / file.deleted
with payload {name, size}.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services import files as files_service
from graphrag_ui.services.audit import audit
from graphrag_ui.services.files import (
    FileServiceError,
    FileTooLargeError,
    QuotaExceededError,
)
from graphrag_ui.services.projects import get_project_role


class FileOut(BaseModel):
    name: str
    size: int


class FileEntryOut(BaseModel):
    name: str
    size: int
    modified_at: str


class FileListOut(BaseModel):
    files: list[FileEntryOut]
    usage_bytes: int
    quota_bytes: int


def register_files_routes(app):
    # Same conventions as projects_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/files", response_model=FileOut,
                 status_code=status.HTTP_201_CREATED)
    async def upload_file(pid: uuid.UUID, file: UploadFile,
                          db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        data = await file.read()
        try:
            name = await files_service.save_file(project, file.filename or "", data)
        except FileServiceError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
        except (FileTooLargeError, QuotaExceededError) as e:
            # 413 for both single-file cap and project quota (spec §9 error handling)
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(e)) from None
        await audit(db, user.id, "file.uploaded", "project", str(pid),
                    {"name": name, "size": len(data)})
        await db.commit()
        return FileOut(name=name, size=len(data))

    @router.get("/{pid}/files", response_model=FileListOut)
    async def list_files(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        return FileListOut(
            files=[FileEntryOut(**f) for f in await files_service.list_files(project)],
            usage_bytes=files_service.usage_bytes(project),
            quota_bytes=files_service.quota_bytes(),
        )

    @router.delete("/{pid}/files/{filename}",
                   status_code=status.HTTP_204_NO_CONTENT)
    async def delete_file(pid: uuid.UUID, filename: str,
                          db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        try:
            size = await files_service.delete_file(project, filename)
        except FileServiceError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
        except FileNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found") from None
        await audit(db, user.id, "file.deleted", "project", str(pid),
                    {"name": filename, "size": size})
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
