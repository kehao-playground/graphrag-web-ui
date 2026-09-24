"""Question sets and lineage-stable question identity (spec 5.3).

A question never referenced by a run is edited in place; once a run
references it, an edit inserts a new row sharing the original lineage_id
and archives the old one, so a historic run's test_results.question_text
stays the question as asked. Sets and questions are archived, never
hard-deleted on user action — a cascade would destroy exactly the history
question_text exists to protect.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, Question, QuestionSet, TestResult
from graphrag_ui.domain.questions import MAX_QUESTIONS_PER_SET
from graphrag_ui.services.audit import audit
from graphrag_ui.services.project_lock import lock_project


class QuestionSetNotFound(RuntimeError):
    """No live question set with this id in the project."""


class QuestionNotFound(RuntimeError):
    """No live question with this id in the project's live sets."""


class QuestionSetTooLarge(RuntimeError):
    """The set already holds MAX_QUESTIONS_PER_SET live questions."""

    def __init__(self) -> None:
        super().__init__("question set is full")
        self.params = {"max_questions": MAX_QUESTIONS_PER_SET}


async def _commit_edit(session: AsyncSession) -> None:
    # Module-level seam: Task 5's lock-barrier tests park between the
    # reference check and the commit by patching this one function.
    await session.commit()


async def _is_referenced(session: AsyncSession, question_id: uuid.UUID) -> bool:
    # Existence only — never a count over a run's results.
    return (
        await session.execute(select(1).where(TestResult.question_id == question_id).limit(1))
    ).scalar() is not None


async def _set_or_raise(session: AsyncSession, project_id: uuid.UUID, set_id: uuid.UUID):
    qs = (
        await session.execute(
            select(QuestionSet).where(
                QuestionSet.id == set_id,
                QuestionSet.project_id == project_id,
                QuestionSet.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if qs is None:
        raise QuestionSetNotFound(f"no live question set {set_id} in project {project_id}")
    return qs


async def _question_or_raise(
    session: AsyncSession, project_id: uuid.UUID, question_id: uuid.UUID
) -> Question:
    # Scoped through the set: a question of another project (or one living
    # under an archived set) is not found, never resurrected by edit.
    q = (
        await session.execute(
            select(Question)
            .join(QuestionSet, QuestionSet.id == Question.set_id)
            .where(
                Question.id == question_id,
                QuestionSet.project_id == project_id,
                QuestionSet.archived_at.is_(None),
                Question.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if q is None:
        raise QuestionNotFound(f"no live question {question_id} in project {project_id}")
    return q


async def create_set(
    session: AsyncSession, project: Project, name: str, actor_id: uuid.UUID
) -> QuestionSet:
    qs = QuestionSet(project_id=project.id, name=name, created_by=actor_id)
    session.add(qs)
    await session.flush()
    await audit(
        session,
        actor_id,
        "question_set.created",
        "project",
        str(project.id),
        {"set_id": str(qs.id), "name": name},
    )
    await session.commit()
    return qs


async def list_sets(session: AsyncSession, project: Project) -> list[QuestionSet]:
    return list(
        (
            await session.execute(
                select(QuestionSet)
                .where(
                    QuestionSet.project_id == project.id,
                    QuestionSet.archived_at.is_(None),
                )
                .order_by(QuestionSet.created_at, QuestionSet.id)
            )
        ).scalars()
    )


async def archive_set(
    session: AsyncSession, project: Project, set_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    # The same lock as an edit's: an archive is a check-then-act against
    # POST /test-runs materializing a manifest, so it re-checks inside the
    # lock and commits there. It ALWAYS archives rather than deletes —
    # referenced or not — so there is one behavior to reason about; the
    # runs' history lives in test_results, which no archive touches.
    await lock_project(session, project.id)
    qs = await _set_or_raise(session, project.id, set_id)
    qs.archived_at = func.now()
    await audit(
        session,
        actor_id,
        "question_set.archived",
        "project",
        str(project.id),
        {"set_id": str(qs.id), "name": qs.name},
    )
    await session.commit()


async def add_question(
    session: AsyncSession,
    project: Project,
    set_id: uuid.UUID,
    text: str,
    actor_id: uuid.UUID,
) -> Question:
    # The cap and MAX(position)+1 are check-then-act: inside the project
    # lock, like edit/archive, two concurrent adds can neither both pass a
    # full set nor share a position (R2-21).
    await lock_project(session, project.id)
    qs = await _set_or_raise(session, project.id, set_id)
    live = (
        await session.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.set_id == qs.id, Question.archived_at.is_(None))
        )
    ).scalar_one()
    if live >= MAX_QUESTIONS_PER_SET:
        raise QuestionSetTooLarge
    # Max over ALL rows of the set, archived forks included: a fresh
    # question must sort after every row that ever held a slot.
    position = (
        await session.execute(
            select(func.coalesce(func.max(Question.position), -1) + 1).where(
                Question.set_id == qs.id
            )
        )
    ).scalar_one()
    q = Question(
        set_id=qs.id, lineage_id=uuid.uuid4(), text=text, position=position, created_by=actor_id
    )
    session.add(q)
    await session.flush()
    await audit(
        session,
        actor_id,
        "question.created",
        "project",
        str(project.id),
        {"question_id": str(q.id), "set_id": str(qs.id)},
    )
    await session.commit()
    return q


async def edit_question(
    session: AsyncSession,
    project: Project,
    question_id: uuid.UUID,
    text: str,
    actor_id: uuid.UUID,
) -> Question:
    """Edit in place, or fork the lineage when a run already references it.

    The reference check is a check-then-act, so it runs INSIDE the project
    lock: without it a PATCH could find the question unreferenced, pause,
    let POST /test-runs commit a manifest referencing it, and then edit in
    place - the exact violation this rule exists to prevent (spec 5.3).
    """
    await lock_project(session, project.id)
    q = await _question_or_raise(session, project.id, question_id)
    if not await _is_referenced(session, q.id):
        q.text = text
        await audit(
            session,
            actor_id,
            "question.updated",
            "project",
            str(project.id),
            {"question_id": str(q.id)},
        )
        await _commit_edit(session)
        return q
    q.archived_at = func.now()
    forked = Question(
        set_id=q.set_id,
        lineage_id=q.lineage_id,
        text=text,
        position=q.position,
        created_by=actor_id,
    )
    session.add(forked)
    await audit(
        session,
        actor_id,
        "question.forked",
        "project",
        str(project.id),
        {"lineage_id": str(q.lineage_id)},
    )
    await _commit_edit(session)
    return forked


async def archive_question(
    session: AsyncSession, project: Project, question_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    # Same lock and same reason as edit_question: archiving decides
    # against the same manifest materialization the edit's fork does.
    await lock_project(session, project.id)
    q = await _question_or_raise(session, project.id, question_id)
    q.archived_at = func.now()
    await audit(
        session,
        actor_id,
        "question.archived",
        "project",
        str(project.id),
        {"question_id": str(q.id)},
    )
    await session.commit()


async def live_questions(
    session: AsyncSession, project_id: uuid.UUID, set_id: uuid.UUID
) -> list[Question]:
    """The set's live questions in ask order — the manifest a run snapshots.

    Scoped to the project: a set id from another project is not found, so
    neither the listing route nor a run's manifest can read across projects.
    Raises QuestionSetNotFound for an unknown or archived set: enqueueing a
    run against a set that is gone is a caller error, not an empty run."""
    qs = (
        await session.execute(
            select(QuestionSet).where(
                QuestionSet.id == set_id,
                QuestionSet.project_id == project_id,
                QuestionSet.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if qs is None:
        raise QuestionSetNotFound(f"no live question set {set_id} in project {project_id}")
    return list(
        (
            await session.execute(
                select(Question)
                .where(Question.set_id == set_id, Question.archived_at.is_(None))
                # position alone is not unique (a fork reuses its row's, and
                # adds raced before R2-21): created_at, id keep the order stable
                .order_by(Question.position, Question.created_at, Question.id)
            )
        ).scalars()
    )
