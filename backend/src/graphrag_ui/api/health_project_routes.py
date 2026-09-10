"""Knowledge-base health endpoints (spec 7.5).

GET /api/projects/{pid}/health is the overview's per-project aggregate;
GET /api/projects/health?ids=… carries the compact subset the project list
needs in ONE round trip, filtered to the projects the caller can see —
without it the list would issue one request per project.

Route order is contractual (same hazard as explore's /artifacts/graph):
this module must register BEFORE projects_routes, or GET
/api/projects/health binds to /api/projects/{project_id} and dies on the
uuid parse of "health". Permission: project:view for the single project;
the batch applies the project list's visibility rule per id and silently
drops the rest — a 403 would confirm a hidden project exists."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.services.health import batch_health, project_health
from graphrag_ui.services.projects import get_member_perms

# The batch ceiling (spec 7.5): one overview round trip, not an unbounded
# fan-out; a client asking for more is a bug and gets a 422, not a timeout.
MAX_BATCH_IDS = 200


class FileCountsOut(BaseModel):
    new: int
    modified: int
    indexed: int
    skipped: int
    removed: int
    total: int


class LastIndexOut(BaseModel):
    job_id: str
    type: str
    finished_at: datetime


class ActiveJobOut(BaseModel):
    id: str
    type: str


class RatingsOut(BaseModel):
    good: int
    fair: int
    poor: int
    unrated: int


class LatestRunOut(BaseModel):
    run_id: str
    set_id: str
    method: str
    index_job_id: str | None
    ratings: RatingsOut
    regressions: int


class HealthOut(BaseModel):
    files: FileCountsOut
    ingest_check: str
    has_baseline: bool
    artifacts_stale: bool
    last_index: LastIndexOut | None
    active_job: ActiveJobOut | None
    latest_run: LatestRunOut | None


class BatchFileCountsOut(BaseModel):
    """Exactly the counts the project list renders (spec 7.5): no indexed,
    no total — the list flags faults, the per-project page enumerates."""

    new: int
    modified: int
    removed: int
    skipped: int


class BatchLastIndexOut(BaseModel):
    finished_at: datetime


class BatchHealthEntryOut(BaseModel):
    files: BatchFileCountsOut
    ingest_check: str
    artifacts_stale: bool
    has_baseline: bool
    last_index: BatchLastIndexOut | None


class BatchHealthOut(BaseModel):
    projects: dict[str, BatchHealthEntryOut]


def register_health_project_routes(app):
    # Same conventions as explore_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/health", response_model=BatchHealthOut)
    async def batch(
        # A string, not list[UUID]: the wire format is ids=<uuid,uuid,…>
        # (spec 7.5), and FastAPI >= 0.115 no longer splits list params on
        # commas — declaring a list would force repeated ?ids=… keys and
        # 422 the documented format instead.
        ids: Annotated[str, Query(min_length=1)],
        db: DbSession,
        user: CurrentUser,
    ):
        parsed: list[uuid.UUID] = []
        try:
            parsed = [uuid.UUID(chunk) for chunk in ids.split(",")]
        except ValueError:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "health_invalid_ids",
                "ids must be comma-separated project uuids",
            ) from None
        if len(parsed) > MAX_BATCH_IDS:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "health_too_many_ids",
                f"at most {MAX_BATCH_IDS} project ids per request",
            )
        return {"projects": await batch_health(db, user.user, user.global_perms, parsed)}

    @router.get("/{pid}/health", response_model=HealthOut)
    async def one(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        return await project_health(db, project)

    app.include_router(router)
