"""Query REST endpoints (spec §6.1): POST /api/projects/{pid}/query for the
four search modes, GET .../query/stream for SSE streaming. Permission:
project:view. All failures map to fixed messages — internals stay in
server logs (no-leak posture)."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graphrag_ui.api.deps import (
    CurrentUser,
    ProjectView,
    SseProjectView,
    SseUser,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError, api_error_for
from graphrag_ui.api.schemas import QueryOut
from graphrag_ui.domain.questions import MAX_QUESTION_CHARS
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.query import QueryError, run_query, stream_query

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


def _query_error_http(exc: QueryError) -> ApiError:
    """A pipeline failure's fixed message; its detail (exception tail)
    stays server-side. Coded refusals (rate limit, not indexed) go through
    the app-level table instead."""
    if exc.code == "config":
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


def _refusal_frame(err: ApiError) -> str:
    """A pre-stream refusal as the stream's only frame: the HTTP error body
    ({detail, code, params?}) under `event: error` (i18n spec §4.3)."""
    data: dict[str, object] = {"detail": err.detail, "code": err.code}
    if err.params:
        data["params"] = err.params
    return f"event: error\ndata: {json.dumps(data)}\n\n"


def register_query_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    # The stream route cannot live on this router: the router-level Bearer
    # dependency would 401 the ?ticket= path before the handler runs — it gets
    # its auth from SseUser (header OR ticket) instead.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])
    sse_router = APIRouter(prefix="/api/projects")

    @router.post("/{pid}/query", response_model=QueryOut)
    async def run_project_query(project: ProjectView, body: QueryIn, user: CurrentUser):
        try:
            return await run_query(project, user.user, body.method, body.query, body.response_type)
        except QueryError as exc:
            raise _query_error_http(exc) from None

    @sse_router.get("/{pid}/query/stream")
    async def stream_project_query(
        project: SseProjectView,
        user: SseUser,
        method: Annotated[Method, Query()],
        query: str = Query(min_length=1, max_length=MAX_QUESTION_CHARS),
        response_type: str | None = Query(default=None, max_length=MAX_RESPONSE_TYPE_CHARS),
    ):
        # NOTE: an SSE ticket may travel as ?ticket= (EventSource cannot
        # send headers) — never log this request or echo query params in any
        # error; details are fixed messages only.

        # Prime the generator so pre-stream failures (rate limit, config,
        # frames, adapter) surface HERE. They answer as a stream whose only
        # frame is `event: error` {detail, code}: EventSource never exposes
        # an HTTP error body, so a JSON 429/409 would reach the SPA as a bare
        # transport error (R3-05). Auth/validation stay plain HTTP errors.
        agen = stream_query(project, user.user, method, query, response_type)
        try:
            first = await anext(agen, None)
        except (CodedServiceError, QueryError) as exc:
            await agen.aclose()
            err = _query_error_http(exc) if isinstance(exc, QueryError) else api_error_for(exc)
            if err is None:  # a coded error without a table row: a server bug
                raise
            frame = _refusal_frame(err)

            async def refused():
                yield frame

            return StreamingResponse(
                refused(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

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
