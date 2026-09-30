"""Explore REST endpoints (spec §6.1/§7): GET /api/projects/{pid}/artifacts/*
for server-paginated parquet browsing, full-row detail and the knowledge
graph. Permission: project:view — the same block as the query routes. Route order is contractual: /artifacts/graph registers BEFORE
/artifacts/{table}, otherwise "graph" binds to the path parameter. All
refusals render through the app-level table; a failed read maps to one
fixed message and its adapter tail stays in server logs."""

from fastapi import APIRouter, Depends, Query, status

from graphrag_ui.api.deps import DbSession, ProjectView, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import ArtifactDetailOut, ArtifactPageOut, GraphOut
from graphrag_ui.services.explore import (
    ExploreReadError,
    artifact_detail,
    knowledge_graph,
    list_artifacts,
)


def _read_failed() -> ApiError:
    # detail (exception tail) stays server-side; fixed message only
    return ApiError(
        status.HTTP_502_BAD_GATEWAY, "explore_read_failed", "failed to read the index output"
    )


def register_explore_routes(app):
    # Same conventions as query_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    # MUST register before {table}
    @router.get("/{pid}/artifacts/graph", response_model=GraphOut)
    async def get_graph(
        project: ProjectView,
        db: DbSession,
        level: int | None = Query(default=None),
    ):
        try:
            return await knowledge_graph(db, project, level)
        except ExploreReadError:
            raise _read_failed() from None

    @router.get("/{pid}/artifacts/{table}", response_model=ArtifactPageOut)
    async def list_table(
        project: ProjectView,
        table: str,
        db: DbSession,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        q: str | None = Query(default=None),
        type: str | None = Query(default=None),
        community: int | None = Query(default=None),
    ):
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
        except ExploreReadError:
            raise _read_failed() from None

    @router.get("/{pid}/artifacts/{table}/{hrid}", response_model=ArtifactDetailOut)
    async def get_row_detail(
        project: ProjectView,
        table: str,
        hrid: int,
        db: DbSession,
    ):
        try:
            data = await artifact_detail(db, project, table, hrid)
        except ExploreReadError:
            raise _read_failed() from None
        if data is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "explore_row_not_found", "row not found")
        return data

    app.include_router(router)
