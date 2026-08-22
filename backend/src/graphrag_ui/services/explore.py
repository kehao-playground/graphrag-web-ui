"""Explore use cases (spec §6.1/§7): server-paginated artifact browsing,
full-row detail and the knowledge-graph envelope — each flagged ``stale``
while an index/update job is queued or running.

Thin async wrapper over the duckdb adapter: the sync adapter calls run in
a worker thread (``asyncio.to_thread``) so parquet reads never block the
event loop. No FastAPI here — failures are domain errors the API layer
maps to fixed zh-TW details; adapter tails stay in server logs."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.artifacts import (
    ArtifactsNotIndexedError,
    get_row,
    graph,
    list_rows,
)
from graphrag_ui.adapters.models import Project
from graphrag_ui.domain.artifacts import table_spec
from graphrag_ui.services.jobs import active_job
from graphrag_ui.services.projects import _ws_path

logger = logging.getLogger(__name__)


class UnknownTableError(RuntimeError):
    """Table name is not in the artifact registry (HTTP 404)."""


class UnsupportedFilterError(RuntimeError):
    """Filter param offered on a table whose TableSpec lacks it (HTTP 422)."""


class ExploreReadError(RuntimeError):
    """Unexpected duckdb/parquet failure (HTTP 502).

    Mirrors QueryError: ``code`` names the failing step, ``tail`` is the
    truncated exception text — logged server-side, never sent to clients.
    """

    def __init__(self, code: str, tail: str):
        super().__init__(f"{code}: {tail}")
        self.code = code
        self.tail = tail


def _guard_table(table: str) -> None:
    if table_spec(table) is None:
        raise UnknownTableError(table)


async def _stale(session: AsyncSession, project: Project) -> bool:
    # A queued/running job means parquet files are mid-rewrite — the
    # response tells the UI results may be incomplete.
    return await active_job(session, project.id) is not None


async def list_artifacts(
    session: AsyncSession, project: Project, table: str, *,
    limit: int, offset: int,
    q: str | None = None, type_filter: str | None = None,
    community: int | None = None,
) -> dict:
    spec = table_spec(table)
    if spec is None:
        raise UnknownTableError(table)
    # Guard before stale/IO: an unsupported param is a client contract
    # issue, never worth a parquet read.
    if type_filter is not None and not spec.type_filter:
        raise UnsupportedFilterError(f"type filter on {spec.name}")
    if community is not None and not spec.community_filter:
        raise UnsupportedFilterError(f"community filter on {spec.name}")
    stale = await _stale(session, project)
    try:
        rows, total = await asyncio.to_thread(
            list_rows, _ws_path(project.id), table,
            limit=limit, offset=offset, q=q,
            type_filter=type_filter, community=community,
        )
    except ArtifactsNotIndexedError:
        raise
    except Exception as exc:  # corrupt parquet etc. — 502, tail stays logged
        logger.exception("explore list failed (project %s, table %s)",
                         project.id, table)
        raise ExploreReadError("list", str(exc)[-500:]) from exc
    return {"rows": rows, "total": total, "stale": stale}


async def artifact_detail(
    session: AsyncSession, project: Project, table: str, hrid: int,
) -> dict | None:
    """Full row envelope, or None when no row carries the hrid (→ 404)."""
    _guard_table(table)
    stale = await _stale(session, project)
    try:
        row = await asyncio.to_thread(get_row, _ws_path(project.id), table, hrid)
    except ArtifactsNotIndexedError:
        raise
    except Exception as exc:
        logger.exception("explore detail failed (project %s, table %s)",
                         project.id, table)
        raise ExploreReadError("detail", str(exc)[-500:]) from exc
    return None if row is None else {"row": row, "stale": stale}


async def knowledge_graph(
    session: AsyncSession, project: Project, level: int | None = None,
) -> dict:
    stale = await _stale(session, project)
    try:
        data = await asyncio.to_thread(graph, _ws_path(project.id), level)
    except ArtifactsNotIndexedError:
        raise
    except Exception as exc:
        logger.exception("explore graph failed (project %s)", project.id)
        raise ExploreReadError("graph", str(exc)[-500:]) from exc
    return {**data, "stale": stale}
