"""File endpoints: upload/list/delete project input files (spec §6.3).

Permissions: upload/delete are project:edit_content, listing is
project:view. Audit actions: file.uploaded / file.deleted
with payload {name, size}.
"""

from fastapi import APIRouter, Depends, Response, UploadFile, status

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    ProjectEditContent,
    ProjectView,
    get_current_user,
)
from graphrag_ui.api.schemas import (
    BulkDeleteIn,
    BulkDeleteOut,
    FileEntryOut,
    FileListOut,
    FileOut,
    PreviewIn,
    PreviewOut,
    TagCatalogOut,
    TagOut,
    TagsIn,
)
from graphrag_ui.services import file_listing, file_preview, file_tags
from graphrag_ui.services import files as files_service
from graphrag_ui.services.files import max_file_bytes


def register_files_routes(app):
    # Same conventions as projects_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    # Service errors (bad name 400, missing file 404, size/quota 413, frozen
    # project 409) render through the app-level table in api/errors.py; the
    # early 413 on a declared oversize is api/middleware.UploadSizeGuard.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
    async def upload_file(
        project: ProjectEditContent, file: UploadFile, db: DbSession, user: CurrentUser
    ):
        # the UploadFile streams through save_file in fixed chunks;
        # nothing larger than one chunk is ever held in memory
        name, size = await files_service.save_file(
            db, project, file.filename or "", file, actor_id=user.id
        )
        return FileOut(name=name, size=size)

    @router.get("/{pid}/files", response_model=FileListOut)
    async def list_files(project: ProjectView, db: DbSession):
        listing = await file_listing.list_files(db, project)
        return FileListOut(
            files=[FileEntryOut(**f) for f in listing["files"]],
            usage_bytes=await files_service.usage_bytes(project),
            quota_bytes=files_service.quota_bytes(),
            max_file_bytes=max_file_bytes(),
            ingest_check=listing["ingest_check"],
            has_baseline=listing["has_baseline"],
        )

    @router.delete("/{pid}/files/{filename}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_file(
        project: ProjectEditContent, filename: str, db: DbSession, user: CurrentUser
    ):
        await files_service.delete_file(db, project, filename, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{pid}/files/{filename}/tags", status_code=status.HTTP_204_NO_CONTENT)
    async def add_file_tags(
        project: ProjectEditContent, filename: str, body: TagsIn, db: DbSession, user: CurrentUser
    ):
        # Tags are metadata, not input (spec 8): no 409 while an index runs.
        await file_tags.add_tags(db, project, filename, body.tags, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/{pid}/files/{filename}/tags", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_file_tags(
        project: ProjectEditContent, filename: str, body: TagsIn, db: DbSession, user: CurrentUser
    ):
        await file_tags.remove_tags(db, project, filename, body.tags, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/tags", response_model=TagCatalogOut)
    async def list_tags(project: ProjectView, db: DbSession):
        return TagCatalogOut(tags=[TagOut(**t) for t in await file_tags.list_tags(db, project)])

    @router.post("/{pid}/files:bulk-delete", response_model=BulkDeleteOut)
    async def bulk_delete_files(
        project: ProjectEditContent, body: BulkDeleteIn, db: DbSession, user: CurrentUser
    ):
        # Bulk delete IS input: same lock and same 409 as a single delete.
        result = await files_service.bulk_delete(db, project, body.names, actor_id=user.id)
        return BulkDeleteOut(**result)

    @router.get("/{pid}/files/{filename}/preview", response_model=PreviewOut)
    async def get_file_preview(project: ProjectView, filename: str):
        return PreviewOut(**await file_preview.preview_file(project, filename))

    @router.post("/{pid}/files/{filename}/preview", response_model=PreviewOut)
    async def locate_file_preview(
        project: ProjectView, filename: str, body: PreviewIn, db: DbSession
    ):
        if body.result_id is not None:
            # Historic locator: resolve_stored_passage has already bound the
            # result to THIS project and the entry's stored source_name to
            # THIS filename (spec 7.4); any failed binding is one fixed 404.
            assert body.entry_id is not None, "the validator pairs entry_id with result_id"
            around: str | None = await file_preview.resolve_stored_passage(
                db, project.id, body.result_id, body.entry_id, filename
            )
        else:
            around = body.passage
        return PreviewOut(**await file_preview.preview_file(project, filename, around=around))

    app.include_router(router)
