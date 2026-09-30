"""Ratings of test results (spec 5.3): the authz lookup and the upsert."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import ResultRating, TestResult, TestRun, User
from graphrag_ui.services.audit import audit


async def result_with_project(
    session: AsyncSession, result_id: uuid.UUID
) -> tuple[TestResult, uuid.UUID] | None:
    """A result and its run's project id — the authz resolution path for
    ratings (spec 8): permission is checked against the row's OWN project."""
    row = (
        await session.execute(
            select(TestResult, TestRun.project_id)
            .join(TestRun, TestRun.id == TestResult.run_id)
            .where(TestResult.id == result_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def rate_result(
    session: AsyncSession, result: TestResult, score: str, note: str, actor: User
) -> ResultRating:
    """Upsert the result's ONE current rating and audit test.rated (spec 5.3).

    Project-shared, not per-user: a colleague must see the judgement this
    writes. Who changed what is carried by the audit log, not by row
    versioning, so re-rating overwrites the same row.
    """
    result_id = result.id
    actor_id = actor.id
    now = datetime.now(UTC)
    rating = ResultRating(
        result_id=result_id, score=score, note=note, rated_by=actor_id, rated_at=now
    )
    session.add(rating)
    try:
        await session.flush()
    except IntegrityError:
        # Lost the unique(result_id) race — or re-rating an existing row.
        # Insert-and-map, never check-then-insert (models.py posture): a
        # check-then-insert upsert turns two concurrent first-ratings into
        # an unmapped 500 for the loser.
        await session.rollback()
        existing = (
            await session.execute(select(ResultRating).where(ResultRating.result_id == result_id))
        ).scalar_one()
        existing.score = score
        existing.note = note
        existing.rated_by = actor_id
        existing.rated_at = now
        rating = existing
    await audit(session, actor_id, "test.rated", "test_result", str(result_id), {"score": score})
    await session.commit()
    return rating
