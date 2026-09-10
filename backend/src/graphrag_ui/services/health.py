"""Knowledge-base health aggregates (spec 7.5): the overview's per-project
state, plus the compact subset the project list needs in one round trip.

The per-project aggregate reuses list_files rather than reimplementing the
enumeration — one source of truth for what `removed` and `skipped` mean —
and reports ingest_check WITH has_baseline because their combination
carries a fault neither shows alone: `unavailable_not_indexed` under an
existing baseline means the output once existed and no longer does.
regressions is counted here, server-side, because the overview must state
it without downloading every result of the latest run.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import (
    Job,
    Project,
    ProjectMember,
    Question,
    ResultRating,
    TestResult,
    TestRun,
    User,
)
from graphrag_ui.domain.permissions import sees_all_projects
from graphrag_ui.domain.test_runs import count_regressions
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services.files import list_files
from graphrag_ui.services.index_snapshots import baseline_row

_FILE_STATES = ("new", "modified", "indexed", "skipped", "removed")


async def project_health(session: AsyncSession, project: Project) -> dict:
    """The overview's per-project aggregate (spec 7.5 shape)."""
    listed = await list_files(session, project)
    counts = {state: 0 for state in _FILE_STATES}
    for entry in listed["files"]:
        counts[entry["index_state"]] += 1

    base = await baseline_row(session, project.id)
    # No baseline row: has_baseline is false and stale is meaningless —
    # a failed FIRST index bumps the epoch while promoting nothing, which
    # is rule 3's overview case, not artifact wreckage (spec 7.5).
    artifacts_stale = base is not None and base.artifact_epoch != project.artifact_epoch

    last = (
        await session.execute(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.type.in_(("index", "update")),
                Job.finished_at.is_not(None),
            )
            .order_by(Job.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    active = await jobs_service.active_job(session, project.id)

    return {
        "files": {**counts, "total": len(listed["files"])},
        "ingest_check": listed["ingest_check"],
        "has_baseline": listed["has_baseline"],
        "artifacts_stale": artifacts_stale,
        "last_index": (
            {"job_id": str(last.id), "type": last.type, "finished_at": last.finished_at}
            if last is not None
            else None
        ),
        "active_job": ({"id": str(active.id), "type": active.type} if active is not None else None),
        "latest_run": await _latest_run(session, project.id),
    }


async def batch_health(
    session: AsyncSession,
    user: User,
    global_perms: frozenset[str],
    ids: list[uuid.UUID],
) -> dict[str, dict]:
    """The compact subset for the ids the caller can see (spec 7.5).

    Visibility is the project list's rule applied per id: view_any/act_any
    sees every requested id, anyone else only their memberships. Unknown or
    invisible ids are dropped, not 404ed — the batch exists so that a stale
    project list cannot fail the caller's whole overview.
    """
    stmt = select(Project).where(Project.id.in_(ids)).order_by(Project.created_at, Project.id)
    if not sees_all_projects(global_perms):
        stmt = stmt.join(ProjectMember).where(ProjectMember.user_id == user.id)
    projects = list((await session.execute(stmt)).scalars().all())

    out: dict[str, dict] = {}
    for project in projects:
        health = await project_health(session, project)
        out[str(project.id)] = {
            "files": {
                key: health["files"][key] for key in ("new", "modified", "removed", "skipped")
            },
            "ingest_check": health["ingest_check"],
            "artifacts_stale": health["artifacts_stale"],
            "has_baseline": health["has_baseline"],
            "last_index": (
                {"finished_at": health["last_index"]["finished_at"]}
                if health["last_index"] is not None
                else None
            ),
        }
    return out


async def _latest_run(session: AsyncSession, project_id: uuid.UUID) -> dict | None:
    """The newest run with its rating distribution and the server-side
    regression count against the next-older run (spec 7.5).

    Newest-first uses the matrix window's ordering (Job.queued_at, then
    TestRun.id): TestRun has no created_at, and the id alone is random.
    """
    runs = list(
        (
            await session.execute(
                select(TestRun)
                .join(Job, Job.id == TestRun.job_id)
                .where(TestRun.project_id == project_id)
                .order_by(Job.queued_at.desc(), TestRun.id.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not runs:
        return None
    latest = runs[0]

    rows = (
        await session.execute(
            select(TestResult.run_id, Question.lineage_id, ResultRating.score)
            .join(Question, Question.id == TestResult.question_id)
            .outerjoin(ResultRating, ResultRating.result_id == TestResult.id)
            .where(TestResult.run_id.in_(run.id for run in runs))
        )
    ).all()
    ratings = {score: 0 for score in ("good", "fair", "poor")}
    unrated = 0
    current: dict[str, str] = {}
    before: dict[str, str] = {}
    for run_id, lineage, score in rows:
        if run_id != latest.id:
            # Unrated results contribute nothing to either side: an unrated
            # lineage is not a regression (count_regressions contract).
            if score is not None:
                before[str(lineage)] = score
        elif score is None:
            unrated += 1
        else:
            ratings[score] += 1
            current[str(lineage)] = score

    return {
        "run_id": str(latest.id),
        "set_id": str(latest.set_id),
        "method": latest.method,
        "index_job_id": str(latest.index_job_id) if latest.index_job_id is not None else None,
        "ratings": {**ratings, "unrated": unrated},
        "regressions": count_regressions(before, current),
    }
