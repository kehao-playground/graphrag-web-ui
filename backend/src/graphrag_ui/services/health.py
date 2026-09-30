"""Knowledge-base health aggregates (spec 7.5): the overview's per-project
state, plus the compact subset the project list needs in one round trip.

Both reuse file_listing.file_listings rather than reimplementing the
enumeration — one source of truth for what `removed` and `skipped` mean —
and report ingest_check WITH has_baseline because their combination
carries a fault neither shows alone: `unavailable_not_indexed` under an
existing baseline means the output once existed and no longer does.
regressions is counted here, server-side, because the overview must state it without downloading
every result of the latest run.

The batch reads each table once for all its projects (R1-71): the project
list asks for up to 200 ids per request, and per-project queries made that
an N+1 of N+1s.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import (
    Job,
    Project,
    Question,
    ResultRating,
    TestResult,
    TestRun,
    User,
)
from graphrag_ui.domain.jobs import CLI_JOB_TYPES
from graphrag_ui.domain.test_runs import count_regressions
from graphrag_ui.services import jobs as jobs_service
from graphrag_ui.services.env_file import referenced_key_missing
from graphrag_ui.services.file_listing import file_listings
from graphrag_ui.services.projects import list_projects

_FILE_STATES = ("new", "modified", "indexed", "skipped", "removed")


async def project_health(session: AsyncSession, project: Project) -> dict:
    """The overview's per-project aggregate (spec 7.5 shape)."""
    listed = (await file_listings(session, [project]))[project.id]
    # last_index is the newest SUCCESS — the output the project answers
    # from; last_attempt is the newest finish of any status, so a failed or
    # cancelled job reads as an attempt, never as a fresh index (R3-06).
    last = (await _last_finished_indexes(session, [project.id], succeeded_only=True)).get(
        project.id
    )
    attempt = (await _last_finished_indexes(session, [project.id], succeeded_only=False)).get(
        project.id
    )
    active = await jobs_service.active_job(session, project.id)

    return {
        "files": _file_counts(listed),
        "ingest_check": listed["ingest_check"],
        "has_baseline": listed["has_baseline"],
        "artifacts_stale": listed["artifacts_stale"],
        "last_index": (
            {"job_id": str(last.id), "type": last.type, "finished_at": last.finished_at}
            if last is not None
            else None
        ),
        "last_attempt": (
            {
                "job_id": str(attempt.id),
                "type": attempt.type,
                "status": attempt.status,
                "finished_at": attempt.finished_at,
            }
            if attempt is not None
            else None
        ),
        "active_job": ({"id": str(active.id), "type": active.type} if active is not None else None),
        "latest_run": await _latest_run(session, project.id),
        "api_key_missing": referenced_key_missing(project),
    }


async def batch_health(
    session: AsyncSession,
    user: User,
    global_perms: frozenset[str],
    ids: list[uuid.UUID],
) -> dict[str, dict]:
    """The compact subset for the ids the caller can see (spec 7.5).

    Visibility is the project list's own rule (list_projects, R1-94).
    Unknown or invisible ids are dropped, not 404ed — the batch exists so
    that a stale project list cannot fail the caller's whole overview.
    """
    projects = await list_projects(session, user, global_perms, ids=ids)
    pids = [p.id for p in projects]
    listings = await file_listings(session, projects)
    last = await _last_finished_indexes(session, pids, succeeded_only=True)
    attempts = await _last_finished_indexes(session, pids, succeeded_only=False)

    out: dict[str, dict] = {}
    for project in projects:
        listed = listings[project.id]
        files = _file_counts(listed)
        index = last.get(project.id)
        attempt = attempts.get(project.id)
        out[str(project.id)] = {
            "files": {key: files[key] for key in ("new", "modified", "removed", "skipped")},
            "ingest_check": listed["ingest_check"],
            "artifacts_stale": listed["artifacts_stale"],
            "has_baseline": listed["has_baseline"],
            "last_index": {"finished_at": index.finished_at} if index is not None else None,
            "last_attempt": (
                {"status": attempt.status, "finished_at": attempt.finished_at}
                if attempt is not None
                else None
            ),
        }
    return out


def _file_counts(listed: dict) -> dict[str, int]:
    counts = {state: 0 for state in _FILE_STATES}
    for entry in listed["files"]:
        counts[entry["index_state"]] += 1
    return {**counts, "total": len(listed["files"])}


async def _last_finished_indexes(
    session: AsyncSession, project_ids: Sequence[uuid.UUID], *, succeeded_only: bool
) -> dict[uuid.UUID, Job]:
    """The newest finished index/update job per project, one query
    (DISTINCT ON); projects without one are absent."""
    if not project_ids:
        return {}
    stmt = select(Job).where(
        Job.project_id.in_(project_ids),
        Job.type.in_(CLI_JOB_TYPES),
        Job.finished_at.is_not(None),
    )
    if succeeded_only:
        stmt = stmt.where(Job.status == "succeeded")
    stmt = stmt.order_by(Job.project_id, Job.finished_at.desc(), Job.id.desc()).distinct(
        Job.project_id
    )
    return {job.project_id: job for job in (await session.execute(stmt)).scalars()}


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
