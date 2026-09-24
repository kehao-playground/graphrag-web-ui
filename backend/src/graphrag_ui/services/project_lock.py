"""Project-row lock and the input freeze (spec 5.2b).

jobs_one_active_per_project serializes job against job; it cannot serialize
job enqueue against filesystem mutation. Both sides therefore take the SAME
project-scoped lock, and the mutating side re-checks for an active job
INSIDE it - the re-check is what closes the check-then-act race, not the
check itself.

The freeze covers index and update only. A test_run job reads output/ and
never input/, so freezing document work for it would be ceremony.

input_mutation() is the one committing shape for work that pairs rows with
a filesystem change: lock -> freeze check -> rows -> flush -> filesystem
step -> commit, rolled back on any failure (spec A1 ordering).
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

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


async def active_job(session: AsyncSession, project_id: uuid.UUID) -> Job | None:
    """Any queued/running job, test_run included - the predicate for work
    that must not overlap a job of any type (project deletion)."""
    return (
        await session.execute(
            select(Job).where(Job.project_id == project_id, Job.status.in_(_ACTIVE)).limit(1)
        )
    ).scalar_one_or_none()


async def assert_input_unfrozen(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Raise ProjectIndexingError when an index/update job holds the project.
    Call this AFTER lock_project within the committing transaction."""
    job = await freezing_job(session, project_id)
    if job is not None:
        raise ProjectIndexingError(str(job.id), job.type)


class InputMutation:
    """Handle yielded by input_mutation; `apply` is how the body runs its
    filesystem step, so the flush before it cannot be forgotten."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def apply[T](self, fs_step: Callable[[], T]) -> T:
        """Flush the rows added so far, then run the sync filesystem step.
        A row the database refuses fails the flush and the step never runs;
        call it last, so nothing but the commit can fail after the change."""
        await self._session.flush()
        return fs_step()


@asynccontextmanager
async def input_mutation(
    session: AsyncSession, project_id: uuid.UUID, *, freeze: bool = True
) -> AsyncIterator[InputMutation]:
    """Take the project lock, re-check the input freeze, yield, commit.

    The body adds its rows (audit included) and ends with
    `await m.apply(<fs step>)`; any exception - a check inside the lock, the
    flush, the filesystem step, the commit - rolls the transaction back and
    propagates. `freeze=False` is for metadata writes (tags) that serialize
    on the lock but are not indexer input (spec 8).

    Residual (spec A1, accepted): a commit failing after the filesystem step
    leaves the change on disk without its rows.
    """
    try:
        await lock_project(session, project_id)
        if freeze:
            await assert_input_unfrozen(session, project_id)
        yield InputMutation(session)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
