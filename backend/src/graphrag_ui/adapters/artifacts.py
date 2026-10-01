"""Read-only DuckDB adapter over graphrag artifact parquet files (spec §6.1/§13).

Reads ``output/*.parquet`` directly with duckdb — no graphrag imports, no
shared state with the query path. This adapter layer owns the duckdb
import (domain/services stay pure). Every dynamic value (file paths,
filter values, limit/offset) is bound with ``?``; the table name never
reaches SQL text — it only selects a registry-validated file name, and
list columns come from the frozen domain registry (schemas probed in
§13 against graphrag 3.1.0).

Every read runs on a cursor of one process-wide in-memory duckdb database
(R1-102): ``connect(":memory:")`` per call built a whole database per
request. A duckdb cursor is a duplicate connection, safe to use from the
worker thread that ``asyncio.to_thread`` hands the call to, one cursor per
call. The database holds no tables; parquet is read with ``read_parquet``.
"""

from __future__ import annotations

import datetime
import threading
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from graphrag_ui.domain.artifacts import TableSpec, table_spec


class ArtifactsNotIndexedError(RuntimeError):
    """Workspace has no indexed parquet for the table (index job not run)."""


_db: duckdb.DuckDBPyConnection | None = None
_db_lock = threading.Lock()


@contextmanager
def _cursor() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh cursor on the shared database, closed after the read."""
    global _db
    with _db_lock:
        if _db is None:
            _db = duckdb.connect(":memory:")
        cur = _db.cursor()
    try:
        yield cur
    finally:
        cur.close()


def _parquet(root: Path, table: str) -> tuple[Path, TableSpec]:
    """Resolve a registered table name to ``(parquet path, spec)``.

    An unknown name is a caller bug (the service layer screens tables
    before calling in); a missing file means the workspace was never
    indexed. Only spec-validated names reach path building.
    """
    spec = table_spec(table)
    if spec is None:
        raise ValueError(f"unknown artifact table: {table!r}")
    assert spec.name == table, "registry key must equal TableSpec.name"
    path = root / "output" / f"{spec.name}.parquet"
    if not path.is_file():
        raise ArtifactsNotIndexedError(f"artifacts not indexed: missing {path}")
    return path, spec


def list_rows(
    root: Path,
    table: str,
    *,
    limit: int,
    offset: int,
    q: str | None = None,
    type_filter: str | None = None,
    community: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of registry-projected rows plus the unpaginated total."""
    path, spec = _parquet(root, table)

    join_sql = ""
    join_params: list[Any] = []
    where_parts: list[str] = []
    where_params: list[Any] = []

    if q:
        like = " OR ".join(f"t.{c} ILIKE '%' || ? || '%'" for c in spec.keyword_fields)
        where_parts.append(f"({like})")
        where_params.extend([q] * len(spec.keyword_fields))
    if type_filter is not None:
        if not spec.type_filter:
            raise ValueError(f"table {table!r} does not support type filtering")
        where_parts.append("t.type = ?")
        where_params.append(type_filter)
    if community is not None:
        if not spec.community_filter:
            raise ValueError(f"table {table!r} does not support community filtering")
        if spec.name == "entities":
            # Entities carry no community column (§13): resolve it through
            # communities.entity_ids at MAX(level) via an inner join.
            comm_path, _ = _parquet(root, "communities")
            join_sql = (
                " INNER JOIN (SELECT UNNEST(entity_ids) AS eid, community"
                " FROM read_parquet(?) WHERE level ="
                " (SELECT MAX(level) FROM read_parquet(?))) AS c ON t.id = c.eid"
            )
            join_params = [str(comm_path), str(comm_path)]
            where_parts.append("c.community = ?")
        else:
            where_parts.append("t.community = ?")
        where_params.append(community)

    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    base_sql = f"FROM read_parquet(?) AS t{join_sql}{where_sql}"
    base_params: list[Any] = [str(path), *join_params, *where_params]
    list_columns = ", ".join(f"t.{c}" for c in spec.list_columns)

    with _cursor() as con:
        # The window total rides the page: one scan instead of COUNT + page.
        cur = con.execute(
            f"SELECT {list_columns}, COUNT(*) OVER () AS _total {base_sql}"
            " ORDER BY t.human_readable_id LIMIT ? OFFSET ?",
            [*base_params, limit, offset],
        )
        names = [d[0] for d in cur.description][:-1]
        fetched = cur.fetchall()
        if fetched:
            total = fetched[0][-1]
        else:
            # An offset past the end returns no row to carry the total.
            count_row = con.execute(f"SELECT COUNT(*) {base_sql}", base_params).fetchone()
            assert count_row is not None, "COUNT(*) always yields one row"
            total = count_row[0]
        rows = [_clean(dict(zip(names, row[:-1]))) for row in fetched]
    return rows, int(total)


def get_row(root: Path, table: str, hrid: int) -> dict[str, Any] | None:
    """Full row (all parquet columns) by human_readable_id, or None."""
    path, _ = _parquet(root, table)
    with _cursor() as con:
        cur = con.execute(
            "SELECT * FROM read_parquet(?) AS t WHERE t.human_readable_id = ?",
            [str(path), hrid],
        )
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
    return _clean(dict(zip(names, row))) if row is not None else None


def graph(root: Path, level: int | None = None, node_limit: int | None = None) -> dict[str, Any]:
    """Knowledge graph: entities as nodes; edges only between known titles.

    Community coloring comes from communities.entity_ids at the chosen
    level (default: the deepest level present). Dangling relationship
    endpoints (a title missing from entities) are dropped.

    At most ``node_limit`` nodes are returned, highest ``degree`` first, with
    ``truncated`` saying whether anything was cut — the WebGL view cannot
    draw a real corpus legibly either. Degree order is what makes a capped
    graph still worth looking at: the hubs survive, and cutting the long
    tail of degree-0 entities first costs the reader nothing.
    ``node_limit=None`` disables the cap.

    The cap, the community lookup and the edge filter all run in duckdb
    (R1-78): Python only ever holds the kept nodes and the edges between
    them, so memory and latency follow ``node_limit``, not the corpus.
    """
    ent_path, _ = _parquet(root, "entities")
    rel_path, _ = _parquet(root, "relationships")
    com_path, _ = _parquet(root, "communities")

    with _cursor() as con:
        levels = [
            int(r[0])
            for r in con.execute(
                "SELECT DISTINCT level FROM read_parquet(?) ORDER BY level",
                [str(com_path)],
            ).fetchall()
        ]
        chosen = level if level is not None else (levels[-1] if levels else 0)
        count_row = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(ent_path)]).fetchone()
        assert count_row is not None, "COUNT(*) always yields one row"
        truncated = node_limit is not None and int(count_row[0]) > node_limit
        # LIMIT NULL is no limit; id breaks degree ties so the cut is stable.
        con.execute(
            "CREATE TEMP TABLE kept AS"
            " SELECT id, human_readable_id, title, type, degree, frequency"
            " FROM read_parquet(?) ORDER BY degree DESC, human_readable_id LIMIT ?",
            [str(ent_path), node_limit],
        )
        try:
            nodes = [
                {
                    "hrid": int(hrid),
                    "title": title,
                    # graph filters key on the type; a null one is "untyped"
                    "type": type_ or "",
                    "degree": int(degree),
                    "frequency": int(frequency),
                    "community": None if comm is None else int(comm),
                }
                for hrid, title, type_, degree, frequency, comm in con.execute(
                    "SELECT k.human_readable_id, k.title, k.type, k.degree, k.frequency,"
                    " c.community FROM kept AS k LEFT JOIN ("
                    "  SELECT eid, ANY_VALUE(community) AS community FROM ("
                    "   SELECT UNNEST(entity_ids) AS eid, community"
                    "   FROM read_parquet(?) WHERE level = ?"
                    "  ) GROUP BY eid"
                    " ) AS c ON k.id = c.eid"
                    " ORDER BY k.degree DESC, k.human_readable_id",
                    [str(com_path), chosen],
                ).fetchall()
            ]
            # Both endpoints must survive the cut: an edge into a node the
            # client never got (or a dangling title) is dropped with it.
            edges = [
                {"source": source, "target": target, "weight": float(weight)}
                for source, target, weight in con.execute(
                    "SELECT r.source, r.target, r.weight FROM read_parquet(?) AS r"
                    " WHERE r.source IN (SELECT title FROM kept)"
                    " AND r.target IN (SELECT title FROM kept)",
                    [str(rel_path)],
                ).fetchall()
            ]
        finally:
            con.execute("DROP TABLE kept")
    return {
        "level": int(chosen),
        "levels": levels,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "node_limit": node_limit,
    }


def read_document_titles(root: Path) -> list[str] | None:
    """documents.title for every indexed document, or None when the parquet
    is absent. One duckdb read of a single column - the whole documents table
    is never loaded (documents is not in FrameCache.TABLES)."""
    path = root / "output" / "documents.parquet"
    if not path.is_file():
        return None
    with _cursor() as con:
        rows = con.execute("SELECT title FROM read_parquet(?)", [str(path)]).fetchall()
    return [str(r[0]) for r in rows if r[0] is not None]


def resolve_document_titles(root: Path, document_ids: Collection[str]) -> dict[str, str]:
    """{document_id: documents.title} for the ids given, in ONE duckdb read.

    The resolver returns TITLES and stops there. Turning a title into a
    filename is the recovery rule of spec 6.3, and that rule must be the one
    that held when the artifacts were built - so the caller applies the
    BASELINE SNAPSHOT's title_recovery, never today's settings.yaml
    (services/citations.py).

    Best-effort: a missing parquet yields {}, which renders citations
    unlinked rather than failing the answer.
    """
    if not document_ids:
        return {}
    path = root / "output" / "documents.parquet"
    if not path.is_file():
        return {}
    ids = list(document_ids)
    placeholders = ", ".join("?" for _ in ids)
    with _cursor() as con:
        rows = con.execute(
            f"SELECT id, title FROM read_parquet(?) WHERE id IN ({placeholders})",
            [str(path), *ids],
        ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows if r[1] is not None}


def _clean(value: Any) -> Any:
    """Coerce a duckdb value into JSON-safe primitives (recursive)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, datetime.date):  # datetime.datetime is a date subclass
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)
