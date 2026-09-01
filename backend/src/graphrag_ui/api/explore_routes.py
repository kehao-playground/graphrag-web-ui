"""Explore REST endpoints (spec §6.1/§7): GET /api/projects/{pid}/artifacts/*
for server-paginated parquet browsing, full-row detail and the knowledge
graph. Permission: project:view — the same block as the query routes. Route order is contractual: /artifacts/graph registers BEFORE
/artifacts/{table}, otherwise "graph" binds to the path parameter. All
failures map to fixed zh-TW details; adapter tails stay in server logs."""

import uuid

from fastapi import APIRouter, Depends, Query, status

from graphrag_ui.adapters.artifacts import ArtifactsNotIndexedError
from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.services.explore import (
    ExploreReadError,
    UnknownTableError,
    UnsupportedFilterError,
    artifact_detail,
    knowledge_graph,
    list_artifacts,
)
from graphrag_ui.services.projects import get_member_perms

_ExploreErrors = (
    UnknownTableError,
    ArtifactsNotIndexedError,
    ExploreReadError,
    UnsupportedFilterError,
)


def _explore_error_http(exc: Exception) -> ApiError:
    """Single error mapping for every explore route (fixed messages)."""
    if isinstance(exc, UnknownTableError):
        return ApiError(status.HTTP_404_NOT_FOUND, "explore_unknown_table", "unknown table")
    if isinstance(exc, ArtifactsNotIndexedError):
        return ApiError(
            status.HTTP_409_CONFLICT, "not_indexed", "not indexed yet — run an indexing job first"
        )
    if isinstance(exc, UnsupportedFilterError):
        return ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "explore_unsupported_filter",
            "this table does not support that filter",
        )
    # detail (exception tail) stays server-side; fixed message only
    return ApiError(
        status.HTTP_502_BAD_GATEWAY, "explore_read_failed", "failed to read the index output"
    )


def register_explore_routes(app):
    # Same conventions as query_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    async def _allowed(db: DbSession, user: CurrentUser, pid: uuid.UUID):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        return project

    @router.get("/{pid}/artifacts/graph")  # MUST register before {table}
    async def get_graph(
        pid: uuid.UUID,
        db: DbSession,
        user: CurrentUser,
        level: int | None = Query(default=None),
    ):
        project = await _allowed(db, user, pid)
        try:
            return await knowledge_graph(db, project, level)
        except _ExploreErrors as exc:
            raise _explore_error_http(exc) from None

    @router.get("/{pid}/artifacts/{table}")
    async def list_table(
        pid: uuid.UUID,
        table: str,
        db: DbSession,
        user: CurrentUser,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        q: str | None = Query(default=None),
        type: str | None = Query(default=None),
        community: int | None = Query(default=None),
    ):
        project = await _allowed(db, user, pid)
        try:
            return await list_artifacts(
                db,
                project,
                table,
                limit=limit,
                offset=offset,
                q=q,
                type_filter=type,
                community=community,
            )
        except _ExploreErrors as exc:
            raise _explore_error_http(exc) from None

    @router.get("/{pid}/artifacts/{table}/{hrid}")
    async def get_row_detail(
        pid: uuid.UUID,
        table: str,
        hrid: int,
        db: DbSession,
        user: CurrentUser,
    ):
        project = await _allowed(db, user, pid)
        try:
            data = await artifact_detail(db, project, table, hrid)
        except _ExploreErrors as exc:
            raise _explore_error_http(exc) from None
        if data is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "explore_row_not_found", "row not found")
        return data

    app.include_router(router)
