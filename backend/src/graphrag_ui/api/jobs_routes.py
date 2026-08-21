"""Jobs REST endpoints (spec §6.1). SSE logs live here too (Task 4 adds the
streaming route). Permission split: start/cancel = editor+ (edit_content),
read/list/logs = viewer+."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from graphrag_ui.adapters.models import Job
from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.api.schemas import (
    JobCreateIn,
    JobOut,
    PreflightOut,
)
from graphrag_ui.domain.jobs import display_status
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services.jobs import DiskWatermarkError, JobConflictError
from graphrag_ui.services.projects import get_project_role


def job_out(j: Job) -> dict:
    # Keys are the API contract (frontend types.ts mirrors them, spec §6.1);
    # argv included so the UI can show the exact CLI invocation.
    return {
        "id": str(j.id),
        "project_id": str(j.project_id),
        "type": j.type,
        "method": j.method,
        "status": j.status,
        "display_status": display_status(j.status, j.cancel_requested_at is not None),
        "cancel_requested_at": j.cancel_requested_at,
        "exit_code": j.exit_code,
        "error": j.error,
        "stats": j.stats,
        "queued_by": str(j.queued_by),
        "queued_at": j.queued_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "argv": j.argv,
    }


async def _job_or_404(db: DbSession, job_id: uuid.UUID) -> Job:
    job = await jobs_service.get(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


async def _job_role(db: DbSession, user: CurrentUser, job: Job) -> str | None:
    return await get_project_role(db, job.project_id, user.id)


def register_jobs_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

    @router.post("/projects/{pid}/jobs", response_model=JobOut, status_code=201)
    async def start_job(pid: uuid.UUID, body: JobCreateIn, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.role, user.is_active, Action.edit_content, await get_project_role(db, pid, user.id)
        ):
            raise _forbidden()
        try:
            job = await jobs_service.enqueue(db, project, body.type, body.method, user)
        except JobConflictError:
            raise HTTPException(status.HTTP_409_CONFLICT, "此專案已有進行中的索引任務") from None
        except DiskWatermarkError:
            raise HTTPException(status.HTTP_409_CONFLICT, "磁碟剩餘空間不足") from None
        return job_out(job)

    @router.get("/projects/{pid}/jobs", response_model=list[JobOut])
    async def list_jobs(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        await _project_or_404(db, pid)
        if not can(
            user.role, user.is_active, Action.view_project, await get_project_role(db, pid, user.id)
        ):
            raise _forbidden()
        return [job_out(j) for j in await jobs_service.list_for_project(db, pid)]

    @router.get("/projects/{pid}/jobs/preflight", response_model=PreflightOut)
    async def preflight(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.role, user.is_active, Action.view_project, await get_project_role(db, pid, user.id)
        ):
            raise _forbidden()
        body = await jobs_service.preflight(db, project)
        body["active_job"] = job_out(body["active_job"]) if body["active_job"] else None
        return body

    @router.get("/jobs/{job_id}", response_model=JobOut)
    async def get_job(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
        job = await _job_or_404(db, job_id)
        if not can(user.role, user.is_active, Action.view_project, await _job_role(db, user, job)):
            raise _forbidden()
        return job_out(job)

    @router.post("/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
        job = await _job_or_404(db, job_id)
        if not can(user.role, user.is_active, Action.edit_content, await _job_role(db, user, job)):
            raise _forbidden()
        if not await jobs_service.cancel(db, job):
            raise HTTPException(status.HTTP_409_CONFLICT, "任務已結束")
        return {"detail": "已請求取消"}

    app.include_router(router)
