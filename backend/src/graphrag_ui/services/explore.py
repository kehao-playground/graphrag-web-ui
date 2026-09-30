"""Explore use cases (spec §6.1/§7): server-paginated artifact browsing,
full-row detail and the knowledge-graph envelope — each flagged ``stale``
while an index/update job is queued or running.

Thin async wrapper over the duckdb adapter: the sync adapter calls run in
a worker thread (``asyncio.to_thread``) so parquet reads never block the
event loop. No FastAPI here — refusals are coded service errors (the
app-level table maps them), a failed read is an ExploreReadError the
routes map to a fixed message; adapter tails stay in server logs."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.artifacts import (
    ArtifactsNotIndexedError,
    get_row,
    graph,
    list_rows,
)
from graphrag_ui.adapters.models import Project
from graphrag_ui.config import get_settings
from graphrag_ui.domain.artifacts import TableSpec, table_spec
from graphrag_ui.services.errors import (
    CodedServiceError,
    NotIndexedError,
    ServicePipelineError,
    not_indexed_on,
    pipeline_step,
)
from graphrag_ui.services.jobs import active_job
from graphrag_ui.services.projects import ws_path


class UnknownTableError(CodedServiceError, LookupError):
    """Table name is not in the artifact registry (HTTP 404)."""

    code = "explore_unknown_table"


class UnsupportedFilterError(CodedServiceError, ValueError):
    """Filter param offered on a table whose TableSpec lacks it (HTTP 422)."""

    code = "explore_unsupported_filter"


class ExploreReadError(ServicePipelineError):
    """Unexpected duckdb/parquet failure (HTTP 502).

    Mirrors QueryError: ``code`` names the failing step, ``detail`` is the
    truncated exception text — logged server-side, never sent to clients.
    """


@contextmanager
def _read_step(step: str, log_msg: str, *log_args: object) -> Iterator[None]:
    """A parquet read: not-indexed is the service's 409, anything else an
    ExploreReadError whose tail stays in the server log."""
    with (
        pipeline_step(ExploreReadError, step, log_msg, *log_args, passthrough=(NotIndexedError,)),
        not_indexed_on(ArtifactsNotIndexedError),
    ):
        yield


def _guard_table(table: str) -> TableSpec:
    spec = table_spec(table)
    if spec is None:
        raise UnknownTableError(table)
    return spec


async def _stale(session: AsyncSession, project: Project) -> bool:
    # A queued/running job means parquet files are mid-rewrite — the
    # response tells the UI results may be incomplete.
    return await active_job(session, project.id) is not None


async def list_artifacts(
    session: AsyncSession,
    project: Project,
    table: str,
    *,
    limit: int,
    offset: int,
    q: str | None = None,
    type_filter: str | None = None,
    community: int | None = None,
) -> dict:
    spec = _guard_table(table)
    # Guard before stale/IO: an unsupported param is a client contract
    # issue, never worth a parquet read.
    if type_filter is not None and not spec.type_filter:
        raise UnsupportedFilterError(f"type filter on {spec.name}")
    if community is not None and not spec.community_filter:
        raise UnsupportedFilterError(f"community filter on {spec.name}")
    stale = await _stale(session, project)
    # corrupt parquet etc. — 502, tail stays logged
    with _read_step("list", "explore list failed (project %s, table %s)", project.id, table):
        rows, total = await asyncio.to_thread(
            list_rows,
            ws_path(project.id),
            table,
            limit=limit,
            offset=offset,
            q=q,
            type_filter=type_filter,
            community=community,
        )
    return {"rows": rows, "total": total, "stale": stale}


async def artifact_detail(
    session: AsyncSession,
    project: Project,
    table: str,
    hrid: int,
) -> dict | None:
    """Full row envelope, or None when no row carries the hrid (→ 404)."""
    _guard_table(table)
    stale = await _stale(session, project)
    with _read_step("detail", "explore detail failed (project %s, table %s)", project.id, table):
        row = await asyncio.to_thread(get_row, ws_path(project.id), table, hrid)
    return None if row is None else {"row": row, "stale": stale}


async def knowledge_graph(
    session: AsyncSession,
    project: Project,
    level: int | None = None,
) -> dict:
    stale = await _stale(session, project)
    # GRAPH_NODE_LIMIT caps what one response may carry; the adapter reports
    # back whether it had to cut, so the UI can say so rather than quietly
    # showing a partial graph as if it were the whole one.
    node_limit = get_settings().graph_node_limit
    with _read_step("graph", "explore graph failed (project %s)", project.id):
        data = await asyncio.to_thread(graph, ws_path(project.id), level, node_limit)
    return {**data, "stale": stale}
