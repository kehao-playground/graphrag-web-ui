"""Query REST endpoint (spec §6.1): POST /api/projects/{pid}/query for the
four search modes. Permission: viewer+ (view_project). All failures map to
fixed zh-TW details — internals stay in server logs (no-leak posture)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from graphrag_ui.adapters.frame_cache import WorkspaceNotIndexedError
from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.projects import get_project_role
from graphrag_ui.services.query import QueryError, run_query
from graphrag_ui.services.rate_limit import QueryRateLimitedError


class QueryIn(BaseModel):
    method: Literal["local", "global", "drift", "basic"]
    query: str = Field(min_length=1)
    response_type: str | None = None


def register_query_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/query")
    async def post_query(pid: uuid.UUID, body: QueryIn, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.role, user.is_active, Action.view_project,
            await get_project_role(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            return await run_query(db, project, user, body.method, body.query, body.response_type)
        except QueryRateLimitedError:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "查詢過於頻繁,請稍後再試") from None
        except WorkspaceNotIndexedError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "尚未建立索引,請先執行索引任務") from None
        except QueryError as exc:
            # detail (exception tail) stays server-side; fixed message only
            if exc.code == "config":
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, "設定載入失敗") from None
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "查詢失敗") from None

    app.include_router(router)
