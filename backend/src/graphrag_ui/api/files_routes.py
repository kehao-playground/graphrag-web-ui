"""File endpoints: upload/list/delete project input files (spec §6.3).

Permissions: upload/delete are project:edit_content, listing is
project:view. Audit actions: file.uploaded / file.deleted
with payload {name, size}.
"""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.config import get_settings
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.services import files as files_service
from graphrag_ui.services.errors import ProjectIndexingError
from graphrag_ui.services.files import (
    PASSAGE_MAX_BYTES,
    FileServiceError,
    FileTooLargeError,
    QuotaExceededError,
    max_file_bytes,
)
from graphrag_ui.services.projects import get_member_perms


class FileOut(BaseModel):
    name: str
    size: int


class FileEntryOut(BaseModel):
    name: str
    # Nullable because a `removed` row has no file behind it (spec 6.1).
    # Inventing a zero size or the deletion timestamp would let the UI sort
    # and total them as if they were files.
    size: int | None
    modified_at: str | None
    sha256: str | None
    index_state: str
    tags: list[str] = []


class FileListOut(BaseModel):
    files: list[FileEntryOut]
    usage_bytes: int
    quota_bytes: int
    # Whether `skipped` can be emitted at all, and why not. On the response,
    # not on each row: it is a property of the artifacts, and repeating it
    # per file would invite the UI to render it per file (spec 6.3).
    ingest_check: str
    has_baseline: bool


# Tags are metadata, not input (spec 8): a tag body is curated vocabulary,
# so each tag is a non-empty bounded string rather than free text.
_TAG = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class TagsIn(BaseModel):
    # extra="forbid": a body with unknown keys is a caller bug, not a
    # silently ignored field (same posture as every other body here).
    model_config = ConfigDict(extra="forbid")

    tags: list[_TAG] = Field(min_length=1)


class BulkDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 500 names is the bulk ceiling (spec 8); each name is validated against
    # the project's whitelist in the service, like every other filename.
    names: list[str] = Field(min_length=1, max_length=500)


class TagOut(BaseModel):
    name: str
    count: int


class TagCatalogOut(BaseModel):
    tags: list[TagOut]


class BulkDeleteOut(BaseModel):
    deleted: int
    bytes: int


class PreviewOut(BaseModel):
    text: str
    offset: int
    total_size: int
    match: bool


class PreviewIn(BaseModel):
    # extra="forbid" so a mixed or unknown-field body is a 422 rather than a
    # silently ignored key (spec 7.4). Slice 1 serves the ad-hoc passage
    # form only; the {result_id, entry_id} locator is slice 3's and is
    # rejected here as an unknown field by design.
    model_config = ConfigDict(extra="forbid")

    passage: str

    @field_validator("passage")
    @classmethod
    def _bounded_bytes(cls, v: str) -> str:
        if not v:
            raise ValueError("passage must not be empty")
        if len(v.encode("utf-8")) > PASSAGE_MAX_BYTES:
            raise ValueError(f"passage exceeds {PASSAGE_MAX_BYTES} bytes")
        return v


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
        if (
            request.method == "POST"
            and _UPLOAD_PATH.match(request.url.path)
            and declared.isdigit()
            and int(declared) > max_file_bytes() + _DECLARED_LENGTH_SLACK
        ):
            return JSONResponse(
                {
                    "detail": (
                        f"file exceeds the {get_settings().upload_max_file_mb} MiB upload limit"
                    ),
                    "code": "file_too_large",
                    "params": {"max_mb": get_settings().upload_max_file_mb},
                },
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )
        return await call_next(request)


def register_files_routes(app):
    # Same conventions as projects_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    _register_upload_size_guard(app)
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
    async def upload_file(
        pid: uuid.UUID, request: Request, file: UploadFile, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            # the UploadFile streams through save_file in fixed chunks;
            # nothing larger than one chunk is ever held in memory
            name, size = await files_service.save_file(
                db, project, file.filename or "", file, actor_id=user.id
            )
        except FileServiceError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e), e.params) from None
        except (FileTooLargeError, QuotaExceededError) as e:
            # 413 for both single-file cap and project quota (spec §9 error handling)
            raise ApiError(status.HTTP_413_CONTENT_TOO_LARGE, e.code, str(e), e.params) from None
        except ProjectIndexingError as e:
            raise ApiError(status.HTTP_409_CONFLICT, e.code, str(e), e.params) from None
        return FileOut(name=name, size=size)

    @router.get("/{pid}/files", response_model=FileListOut)
    async def list_files(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        listing = await files_service.list_files(db, project)
        return FileListOut(
            files=[FileEntryOut(**f) for f in listing["files"]],
            usage_bytes=await files_service.usage_bytes(project),
            quota_bytes=files_service.quota_bytes(),
            ingest_check=listing["ingest_check"],
            has_baseline=listing["has_baseline"],
        )

    @router.delete("/{pid}/files/{filename}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_file(pid: uuid.UUID, filename: str, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            await files_service.delete_file(db, project, filename, actor_id=user.id)
        except FileServiceError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e), e.params) from None
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None
        except ProjectIndexingError as e:
            raise ApiError(status.HTTP_409_CONFLICT, e.code, str(e), e.params) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{pid}/files/{filename}/tags", status_code=status.HTTP_204_NO_CONTENT)
    async def add_tags(
        pid: uuid.UUID, filename: str, body: TagsIn, db: DbSession, user: CurrentUser
    ):
        # Tags are metadata, not input (spec 8): no 409 while an index runs.
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            await files_service.add_tags(db, project, filename, body.tags, actor_id=user.id)
        except FileServiceError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e), e.params) from None
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/{pid}/files/{filename}/tags", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_tags(
        pid: uuid.UUID, filename: str, body: TagsIn, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            await files_service.remove_tags(db, project, filename, body.tags, actor_id=user.id)
        except FileServiceError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e), e.params) from None
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/tags", response_model=TagCatalogOut)
    async def list_tags(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        return TagCatalogOut(tags=[TagOut(**t) for t in await files_service.list_tags(db, project)])

    @router.post("/{pid}/files:bulk-delete", response_model=BulkDeleteOut)
    async def bulk_delete_files(
        pid: uuid.UUID, body: BulkDeleteIn, db: DbSession, user: CurrentUser
    ):
        # Bulk delete IS input: same lock and same 409 as a single delete.
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            result = await files_service.bulk_delete(db, project, body.names, actor_id=user.id)
        except FileServiceError as e:
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e), e.params) from None
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None
        except ProjectIndexingError as e:
            raise ApiError(status.HTTP_409_CONFLICT, e.code, str(e), e.params) from None
        return BulkDeleteOut(**result)

    @router.get("/{pid}/files/{filename}/preview", response_model=PreviewOut)
    async def get_preview(pid: uuid.UUID, filename: str, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            return PreviewOut(**await files_service.preview_file(project, filename))
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None

    @router.post("/{pid}/files/{filename}/preview", response_model=PreviewOut)
    async def post_preview(
        pid: uuid.UUID, filename: str, body: PreviewIn, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            return PreviewOut(
                **await files_service.preview_file(project, filename, around=body.passage)
            )
        except FileNotFoundError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "file_not_found", "file not found") from None

    app.include_router(router)
