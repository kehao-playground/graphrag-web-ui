"""File endpoints: upload/list/delete project input files (spec §6.3).

Permissions: upload/delete are editor+ (Action.edit_content), listing is
viewer+ (Action.view_project). Audit actions: file.uploaded / file.deleted
with payload {name, size}.
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services import files as files_service
from graphrag_ui.services.audit import audit
from graphrag_ui.services.files import (
    FileServiceError,
    FileTooLargeError,
    QuotaExceededError,
    max_file_bytes,
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


# POST /api/projects/{pid}/files — the only upload endpoint (pid is a path
# segment, so [^/]+ cannot over-match into deeper routes).
_UPLOAD_PATH = re.compile(r"^/api/projects/[^/]+/files$")

# Multipart framing (boundary + part headers + trailing CRLF) inflates
# Content-Length slightly beyond the payload; tolerate it so an
# exactly-at-cap file is not falsely rejected by the early check. The
# authoritative cap is the streaming limit in save_file.
_DECLARED_LENGTH_SLACK = 64 * 1024


def _register_upload_size_guard(app):
    """Early 413 on a declared-oversized upload, before the body is read.

    FastAPI parses the whole multipart body ahead of endpoint code, so a
    check inside upload_file would fire only after a multi-GB body had been
    spooled and parsed. Rejecting at the middleware layer keeps a hostile
    POST this cheap: header read, response, done. The header is advisory
    (may be absent or malformed — chunked uploads fall through); the
    streaming cap in save_file remains the authoritative limit.
    """

    @app.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):
        declared = request.headers.get("content-length", "")
        if (request.method == "POST" and _UPLOAD_PATH.match(request.url.path)
                and declared.isdigit()
                and int(declared) > max_file_bytes() + _DECLARED_LENGTH_SLACK):
            return JSONResponse(
                {"detail": (f"file exceeds the "
                            f"{get_settings().upload_max_file_mb} MiB upload limit")},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        return await call_next(request)


def register_files_routes(app):
    # Same conventions as projects_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    _register_upload_size_guard(app)
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/files", response_model=FileOut,
                 status_code=status.HTTP_201_CREATED)
    async def upload_file(pid: uuid.UUID, request: Request, file: UploadFile,
                          db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        try:
            # the UploadFile streams through save_file in fixed chunks;
            # nothing larger than one chunk is ever held in memory
            name, size = await files_service.save_file(project, file.filename or "", file)
        except FileServiceError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
        except (FileTooLargeError, QuotaExceededError) as e:
            # 413 for both single-file cap and project quota (spec §9 error handling)
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(e)) from None
        await audit(db, user.id, "file.uploaded", "project", str(pid),
                    {"name": name, "size": size})
        await db.commit()
        return FileOut(name=name, size=size)

    @router.get("/{pid}/files", response_model=FileListOut)
    async def list_files(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        return FileListOut(
            files=[FileEntryOut(**f) for f in await files_service.list_files(project)],
            usage_bytes=await files_service.usage_bytes(project),
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
