"""Jobs REST endpoints (spec §6.1). SSE logs live here too (Task 4 adds the
streaming route). Permission split: start/cancel = project:run_jobs,
read/list/logs = project:view."""

import json
import uuid

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.index_runner import log_path_for
from graphrag_ui.adapters.job_logs import tail_log
from graphrag_ui.adapters.models import Job
from graphrag_ui.api.deps import CurrentUser, DbSession, SseUser, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.api.schemas import (
    JobCreateIn,
    JobOut,
    PreflightOut,
)
from graphrag_ui.domain.jobs import TERMINAL_STATUSES, display_status
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services.jobs import DiskWatermarkError, JobConflictError
from graphrag_ui.services.projects import get_member_perms, ws_path


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
        raise ApiError(status.HTTP_404_NOT_FOUND, "job_not_found", "job not found")
    return job


async def _job_perms(db: DbSession, user: CurrentUser,
                     job: Job) -> frozenset[str] | None:
    return await get_member_perms(db, job.project_id, user.id)


def register_jobs_routes(app):
    # Same conventions as dry_run_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    # The SSE logs route below cannot live on this router: the router-level
    # Bearer dependency would 401 the ?token= path before the handler runs.
    router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])
    sse_router = APIRouter(prefix="/api")

    @router.post("/projects/{pid}/jobs", response_model=JobOut, status_code=201)
    async def start_job(pid: uuid.UUID, body: JobCreateIn, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms, user.is_active, Atom.project_run_jobs,
            await get_member_perms(db, pid, user.id)
        ):
            raise _forbidden()
        try:
            job = await jobs_service.enqueue(db, project, body.type, body.method, user.user)
        except JobConflictError:
            raise ApiError(status.HTTP_409_CONFLICT, "job_conflict", "此專案已有進行中的索引任務") from None
        except DiskWatermarkError:
            raise ApiError(status.HTTP_409_CONFLICT, "disk_watermark", "磁碟剩餘空間不足") from None
        return job_out(job)

    @router.get("/projects/{pid}/jobs", response_model=list[JobOut])
    async def list_jobs(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        await _project_or_404(db, pid)
        if not can(
            user.global_perms, user.is_active, Atom.project_view,
            await get_member_perms(db, pid, user.id)
        ):
            raise _forbidden()
        return [job_out(j) for j in await jobs_service.list_for_project(db, pid)]

    @router.get("/projects/{pid}/jobs/preflight", response_model=PreflightOut)
    async def preflight(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms, user.is_active, Atom.project_view,
            await get_member_perms(db, pid, user.id)
        ):
            raise _forbidden()
        body = await jobs_service.preflight(db, project)
        body["active_job"] = job_out(body["active_job"]) if body["active_job"] else None
        return body

    @router.get("/jobs/{job_id}", response_model=JobOut)
    async def get_job(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
        job = await _job_or_404(db, job_id)
        if not can(user.global_perms, user.is_active, Atom.project_view,
                   await _job_perms(db, user, job)):
            raise _forbidden()
        return job_out(job)

    @router.post("/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
        job = await _job_or_404(db, job_id)
        if not can(user.global_perms, user.is_active, Atom.project_run_jobs,
                   await _job_perms(db, user, job)):
            raise _forbidden()
        if not await jobs_service.cancel(db, job):
            raise ApiError(status.HTTP_409_CONFLICT, "job_already_finished", "任務已結束")
        return {"detail": "已請求取消"}


    @sse_router.get("/jobs/{job_id}/logs")
    async def job_logs(
        job_id: uuid.UUID,
        db: DbSession,
        user: SseUser,
        last_event_id: str | None = Header(default=None),
        offset: int = -1,
    ):
        job = await _job_or_404(db, job_id)
        if not can(user.global_perms, user.is_active, Atom.project_view,
                   await _job_perms(db, user, job)):
            raise _forbidden()
        # ?offset= (tests) wins over the Last-Event-ID header; -1 = not given.
        try:
            start = offset if offset >= 0 else int(last_event_id or 0)
        except ValueError:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "job_invalid_last_event_id", "invalid Last-Event-ID") from None
        log_path = log_path_for(ws_path(job.project_id), job.id)

        async def gen():
            # NOTE: db session is request-scoped and may close once the
            # response starts streaming — poll liveness in a fresh session.
            async def finished() -> bool:
                async with get_session_factory()() as s:
                    fresh = await jobs_service.get(s, job_id)
                return fresh is None or fresh.status in TERMINAL_STATUSES

            pos = start
            async for pos, chunk in tail_log(log_path, start, finished=finished):
                # SSE data lines are single-line; json.dumps escapes newlines
                yield f"id: {pos}\nevent: log\ndata: {json.dumps(chunk.decode(errors='replace'))}\n\n"
            async with get_session_factory()() as s:
                final = await jobs_service.get(s, job_id)
            status_str = final.status if final is not None else "terminal"
            yield f"event: done\ndata: {json.dumps({'offset': pos, 'status': status_str})}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.include_router(router)
    app.include_router(sse_router)
