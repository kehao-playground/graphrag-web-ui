"""Run, result and rating endpoints (spec 5.3/8).

Reading is project:view; POST /test-runs spends compute (project:run_jobs,
the atom that gates indexing); ratings curate content
(project:edit_content). The rid-addressed routes resolve permission through
the row's OWN project and 404 on any mismatch — a 403 would confirm the
row exists (spec 8). Audit action: test.rated.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    Principal,
    ProjectRunJobs,
    ProjectView,
    forbidden,
    get_current_user,
)
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.query_routes import Method
from graphrag_ui.api.schemas import CitationOut, QueryTimingsOut, UuidStr
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.domain.test_runs import MATRIX_DEFAULT_RUNS
from graphrag_ui.services import ratings as ratings_service
from graphrag_ui.services import test_runs as test_runs_service
from graphrag_ui.services.projects import get_member_perms


class RunIn(BaseModel):
    # extra="forbid": a body with unknown keys is a caller bug, not a
    # silently ignored field (same posture as every other body here).
    model_config = ConfigDict(extra="forbid")

    set_id: uuid.UUID
    method: Method


class RatingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Literal spelling of domain.test_runs.RATING_SCORES (mypy cannot
    # star-expand a tuple into Literal); the 422 and the OpenAPI enum
    # both come from here.
    score: Literal["good", "fair", "poor"]
    note: str = ""


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UuidStr
    set_id: UuidStr
    job_id: UuidStr
    index_job_id: UuidStr | None
    method: str
    workspace_config_revision: str | None
    started_at: datetime | None
    finished_at: datetime | None


class CellOut(BaseModel):
    result_id: str
    question_text: str
    rating: str | None
    error: str | None
    completed: bool


class RowOut(BaseModel):
    lineage_id: str
    cells: list[CellOut | None]


class MatrixOut(BaseModel):
    runs: list[RunOut]
    rows: list[RowOut]


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: str
    note: str
    rated_by: UuidStr
    rated_at: datetime


class ResultOut(BaseModel):
    id: str
    position: int
    question_id: str
    question_text: str
    answer: str | None
    citations: list[CitationOut] | None
    timings: QueryTimingsOut | None
    error: str | None
    completed_at: datetime | None
    rating: RatingOut | None


class ResultListOut(BaseModel):
    results: list[ResultOut]


async def _readable_run_or_404(db: AsyncSession, user: Principal, run_id: uuid.UUID):
    # Unknown and unreadable are indistinguishable on purpose (spec 8):
    # the route is rid-addressed, so a 403 would confirm the run exists.
    run = await test_runs_service.get_run(db, run_id)
    if run is not None and can(
        user.global_perms, Atom.project_view, await get_member_perms(db, run.project_id, user.id)
    ):
        return run
    raise ApiError(status.HTTP_404_NOT_FOUND, "test_run_not_found", "test run not found")


def register_test_runs_routes(app):
    # Same conventions as questions_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

    @router.get("/projects/{pid}/test-runs", response_model=MatrixOut)
    async def get_matrix(
        project: ProjectView,
        db: DbSession,
        runs: Annotated[int, Query(ge=1)] = MATRIX_DEFAULT_RUNS,
    ):
        window, matrix_rows = await test_runs_service.run_matrix(db, project.id, runs)
        return MatrixOut(
            runs=[RunOut.model_validate(r) for r in window],
            rows=[
                RowOut(
                    lineage_id=str(lineage_id),
                    cells=[
                        None
                        if cell is None
                        else CellOut(
                            result_id=str(cell[0].id),
                            question_text=cell[0].question_text,
                            rating=cell[1],
                            error=cell[0].error,
                            completed=cell[0].completed_at is not None,
                        )
                        for cell in cells
                    ],
                )
                for lineage_id, cells in matrix_rows
            ],
        )

    @router.post("/projects/{pid}/test-runs", response_model=RunOut, status_code=201)
    async def start_run(project: ProjectRunJobs, body: RunIn, db: DbSession, user: CurrentUser):
        # 400 question_set_empty, 404 question_set_not_found and 409
        # job_conflict render through the app-level table. The conflict has
        # no type predicate: an index, an update or another test run holds
        # the project (spec 5.4).
        run = await test_runs_service.enqueue_run(db, project, body.set_id, body.method, user.user)
        return RunOut.model_validate(run)

    @router.get("/test-runs/{rid}/results", response_model=ResultListOut)
    async def get_results(rid: uuid.UUID, db: DbSession, user: CurrentUser):
        run = await _readable_run_or_404(db, user, rid)
        return ResultListOut(
            results=[
                ResultOut(
                    id=str(result.id),
                    position=result.position,
                    question_id=str(result.question_id),
                    question_text=result.question_text,
                    answer=result.answer,
                    citations=(
                        [CitationOut.model_validate(c) for c in result.citations]
                        if result.citations is not None
                        else None
                    ),
                    timings=(
                        QueryTimingsOut.model_validate(result.timings)
                        if result.timings is not None
                        else None
                    ),
                    error=result.error,
                    completed_at=result.completed_at,
                    rating=RatingOut.model_validate(rating) if rating is not None else None,
                )
                for result, rating in await test_runs_service.list_results(db, run.id)
            ]
        )

    @router.put("/test-results/{rid}/rating", response_model=RatingOut)
    async def put_rating(rid: uuid.UUID, body: RatingIn, db: DbSession, user: CurrentUser):
        # Permission resolves through the result's OWN project (spec 8):
        # run_id -> project_id. Unknown and unreadable are 404; a caller
        # who can view but not curate gets the plain 403.
        ctx = await ratings_service.result_with_project(db, rid)
        if ctx is not None:
            result, project_id = ctx
            perms = await get_member_perms(db, project_id, user.id)
            if can(user.global_perms, Atom.project_edit_content, perms):
                rating = await ratings_service.rate_result(
                    db, result, body.score, body.note, user.user
                )
                return RatingOut.model_validate(rating)
            if can(user.global_perms, Atom.project_view, perms):
                raise forbidden()
        raise ApiError(status.HTTP_404_NOT_FOUND, "test_result_not_found", "test result not found")

    app.include_router(router)
