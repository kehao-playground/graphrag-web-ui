"""Project-row lock and the input freeze (spec 5.2b).

jobs_one_active_per_project serializes job against job; it cannot serialize
job enqueue against filesystem mutation. Both sides therefore take the SAME
project-scoped lock, and the mutating side re-checks for an active job
INSIDE it - the re-check is what closes the check-then-act race, not the
check itself.

The freeze covers index and update only. A test_run job reads output/ and
never input/, so freezing document work for it would be ceremony.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Job, Project
from graphrag_ui.services.errors import ProjectIndexingError

FREEZING_JOB_TYPES: tuple[str, ...] = ("index", "update")
_ACTIVE = ("queued", "running")


async def lock_project(session: AsyncSession, project_id: uuid.UUID) -> None:
    """SELECT ... FOR UPDATE on the project row. Held until the caller's
    transaction ends, which is why callers stream uploads OUTSIDE it."""
    await session.execute(select(Project.id).where(Project.id == project_id).with_for_update())


async def freezing_job(session: AsyncSession, project_id: uuid.UUID) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(
                Job.project_id == project_id,
                Job.status.in_(_ACTIVE),
                Job.type.in_(FREEZING_JOB_TYPES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def assert_input_unfrozen(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Raise ProjectIndexingError when an index/update job holds the project.
    Call this AFTER lock_project within the committing transaction."""
    job = await freezing_job(session, project_id)
    if job is not None:
        raise ProjectIndexingError(str(job.id), job.type)
