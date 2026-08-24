"""Query REST endpoints (spec §6.1): POST /api/projects/{pid}/query for the
four search modes, GET .../query/stream for SSE streaming. Permission:
viewer+ (view_project). All failures map to fixed zh-TW details — internals
stay in server logs (no-leak posture)."""

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graphrag_ui.adapters.frame_cache import WorkspaceNotIndexedError
from graphrag_ui.api.deps import CurrentUser, DbSession, SseUser, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.projects import get_project_role
from graphrag_ui.services.query import QueryError, run_query, stream_query
from graphrag_ui.services.rate_limit import QueryRateLimitedError

Method = Literal["local", "global", "drift", "basic"]


class QueryIn(BaseModel):
    method: Method
    query: str = Field(min_length=1)
    response_type: str | None = None


def _query_error_http(exc: Exception) -> ApiError:
    """Single error mapping for both query paths (POST + SSE pre-stream)."""
    if isinstance(exc, QueryRateLimitedError):
        return ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "query_rate_limited", "查詢過於頻繁,請稍後再試")
    if isinstance(exc, WorkspaceNotIndexedError):
        return ApiError(status.HTTP_409_CONFLICT, "not_indexed", "尚未建立索引,請先執行索引任務")
    # detail (exception tail) stays server-side; fixed message only
    if isinstance(exc, QueryError) and exc.code == "config":
        return ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR, "query_config_failed", "設定載入失敗")
    return ApiError(status.HTTP_502_BAD_GATEWAY, "query_failed", "查詢失敗")


def _format_event(kind: str, payload) -> str:
    """One SSE frame. Data lines are single-line; json.dumps escapes newlines
    (same convention as the job-log stream). The error event wraps its fixed
    message plus the machine code in {"detail", "code"} (spec §4.3)."""
    data = ({"detail": payload, "code": "query_interrupted"}
            if kind == "error" else payload)
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


def register_query_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    # The stream route cannot live on this router: the router-level Bearer
    # dependency would 401 the ?token= path before the handler runs — it gets
    # its auth from SseUser (header OR query token) instead.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])
    sse_router = APIRouter(prefix="/api/projects")

    async def _prepare_query(db: DbSession, user: CurrentUser, pid: uuid.UUID):
        """Shared pre-check for both query paths: project-or-404 + viewer+."""
        project = await _project_or_404(db, pid)
        if not can(
            user.role, user.is_active, Action.view_project,
            await get_project_role(db, pid, user.id),
        ):
            raise _forbidden()
        return project

    @router.post("/{pid}/query")
    async def post_query(pid: uuid.UUID, body: QueryIn, db: DbSession, user: CurrentUser):
        project = await _prepare_query(db, user, pid)
        try:
            return await run_query(project, user, body.method, body.query, body.response_type)
        except (QueryRateLimitedError, WorkspaceNotIndexedError, QueryError) as exc:
            raise _query_error_http(exc) from None

    @sse_router.get("/{pid}/query/stream")
    async def get_query_stream(
        pid: uuid.UUID,
        db: DbSession,
        user: SseUser,
        method: Annotated[Method, Query()],
        query: str = Query(min_length=1),
        response_type: str | None = Query(default=None),
    ):
        # NOTE: the access token may travel as ?token= (EventSource cannot
        # send headers) — never log this request or echo query params in any
        # error; details are fixed messages only.
        project = await _prepare_query(db, user, pid)

        # Prime the generator so pre-stream failures (rate limit, config,
        # frames, adapter) raise HERE as plain JSON HTTP errors — the 200 +
        # text/event-stream response must not have started yet.
        agen = stream_query(project, user, method, query, response_type)
        try:
            first = await anext(agen, None)
        except (QueryRateLimitedError, WorkspaceNotIndexedError, QueryError) as exc:
            await agen.aclose()
            raise _query_error_http(exc) from None

        async def sse():
            try:
                if first is not None:
                    yield _format_event(first[0], first[1])
                async for kind, payload in agen:
                    yield _format_event(kind, payload)
            finally:
                await agen.aclose()

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.include_router(router)
    app.include_router(sse_router)
