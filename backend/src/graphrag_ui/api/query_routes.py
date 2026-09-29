"""Query REST endpoints (spec §6.1): POST /api/projects/{pid}/query for the
four search modes, GET .../query/stream for SSE streaming. Permission:
project:view. All failures map to fixed zh-TW details — internals
stay in server logs (no-leak posture)."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graphrag_ui.adapters.frame_cache import WorkspaceNotIndexedError
from graphrag_ui.api.deps import (
    CurrentUser,
    ProjectView,
    SseProjectView,
    SseUser,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.schemas import QueryOut
from graphrag_ui.domain.questions import MAX_QUESTION_CHARS
from graphrag_ui.services.query import QueryError, run_query, stream_query
from graphrag_ui.services.rate_limit import QueryRateLimitedError

Method = Literal["local", "global", "drift", "basic"]


# graphrag's response_type is a short format phrase ("multiple
# paragraphs"); the bound keeps a paste from becoming LLM prompt cost.
MAX_RESPONSE_TYPE_CHARS = 100


class QueryIn(BaseModel):
    # The same bound as a saved question (domain.questions): the ad-hoc
    # path is not a way around it.
    method: Method
    query: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    response_type: str | None = Field(default=None, max_length=MAX_RESPONSE_TYPE_CHARS)


def _query_error_http(exc: Exception) -> ApiError:
    """Single error mapping for both query paths (POST + SSE pre-stream)."""
    if isinstance(exc, QueryRateLimitedError):
        return ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "query_rate_limited",
            "too many queries — please retry later",
        )
    if isinstance(exc, WorkspaceNotIndexedError):
        return ApiError(
            status.HTTP_409_CONFLICT, "not_indexed", "not indexed yet — run an indexing job first"
        )
    # detail (exception tail) stays server-side; fixed message only
    if isinstance(exc, QueryError) and exc.code == "config":
        return ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "query_config_failed", "failed to load settings"
        )
    return ApiError(status.HTTP_502_BAD_GATEWAY, "query_failed", "query failed")


def _format_event(kind: str, payload) -> str:
    """One SSE frame. Data lines are single-line; json.dumps escapes newlines
    (same convention as the job-log stream). The error event wraps its fixed
    message plus the machine code in {"detail", "code"} (spec §4.3)."""
    data = {"detail": payload, "code": "query_interrupted"} if kind == "error" else payload
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


def register_query_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    # The stream route cannot live on this router: the router-level Bearer
    # dependency would 401 the ?token= path before the handler runs — it gets
    # its auth from SseUser (header OR query token) instead.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])
    sse_router = APIRouter(prefix="/api/projects")

    @router.post("/{pid}/query", response_model=QueryOut)
    async def post_query(project: ProjectView, body: QueryIn, user: CurrentUser):
        try:
            return await run_query(project, user.user, body.method, body.query, body.response_type)
        except (QueryRateLimitedError, WorkspaceNotIndexedError, QueryError) as exc:
            raise _query_error_http(exc) from None

    @sse_router.get("/{pid}/query/stream")
    async def get_query_stream(
        project: SseProjectView,
        user: SseUser,
        method: Annotated[Method, Query()],
        query: str = Query(min_length=1, max_length=MAX_QUESTION_CHARS),
        response_type: str | None = Query(default=None, max_length=MAX_RESPONSE_TYPE_CHARS),
    ):
        # NOTE: the access token may travel as ?token= (EventSource cannot
        # send headers) — never log this request or echo query params in any
        # error; details are fixed messages only.

        # Prime the generator so pre-stream failures (rate limit, config,
        # frames, adapter) raise HERE as plain JSON HTTP errors — the 200 +
        # text/event-stream response must not have started yet.
        agen = stream_query(project, user.user, method, query, response_type)
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
