"""Read-only DuckDB adapter over graphrag artifact parquet files (spec §6.1/§13).

Reads ``output/*.parquet`` directly with duckdb — no graphrag imports, no
shared state with the query path. This adapter layer owns the duckdb
import (domain/services stay pure). Every dynamic value (file paths,
filter values, limit/offset) is bound with ``?``; the table name never
reaches SQL text — it only selects a registry-validated file name, and
list columns come from the frozen domain registry (schemas probed in
§13 against graphrag 3.1.0).
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from graphrag_ui.domain.artifacts import TableSpec, table_spec


class ArtifactsNotIndexedError(RuntimeError):
    """Workspace has no indexed parquet for the table (index job not run)."""


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

    with duckdb.connect(":memory:") as con:
        total = con.execute(f"SELECT COUNT(*) {base_sql}", base_params).fetchone()[0]
        cur = con.execute(
            f"SELECT {list_columns} {base_sql} ORDER BY t.human_readable_id LIMIT ? OFFSET ?",
            [*base_params, limit, offset],
        )
        names = [d[0] for d in cur.description]
        rows = [_clean(dict(zip(names, row))) for row in cur.fetchall()]
    return rows, int(total)


def get_row(root: Path, table: str, hrid: int) -> dict[str, Any] | None:
    """Full row (all parquet columns) by human_readable_id, or None."""
    path, _ = _parquet(root, table)
    with duckdb.connect(":memory:") as con:
        cur = con.execute(
            "SELECT * FROM read_parquet(?) AS t WHERE t.human_readable_id = ?",
            [str(path), hrid],
        )
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
    return _clean(dict(zip(names, row))) if row is not None else None


def graph(root: Path, level: int | None = None) -> dict[str, Any]:
    """Knowledge graph: every entity is a node; edges only between known titles.

    Community coloring comes from communities.entity_ids at the chosen
    level (default: the deepest level present). Dangling relationship
    endpoints (a title missing from entities) are dropped.
    """
    ent_path, _ = _parquet(root, "entities")
    rel_path, _ = _parquet(root, "relationships")
    com_path, _ = _parquet(root, "communities")

    with duckdb.connect(":memory:") as con:
        levels = [
            int(r[0])
            for r in con.execute(
                "SELECT DISTINCT level FROM read_parquet(?) ORDER BY level",
                [str(com_path)],
            ).fetchall()
        ]
        chosen = level if level is not None else (levels[-1] if levels else 0)
        community_of = {
            eid: int(comm)
            for eid, comm in con.execute(
                "SELECT UNNEST(entity_ids) AS eid, community FROM read_parquet(?) WHERE level = ?",
                [str(com_path), chosen],
            ).fetchall()
        }
        nodes = [
            {
                "hrid": int(hrid),
                "title": title,
                "type": type_,
                "degree": int(degree),
                "frequency": int(frequency),
                "community": community_of.get(eid),
            }
            for eid, hrid, title, type_, degree, frequency in con.execute(
                "SELECT id, human_readable_id, title, type, degree, frequency FROM read_parquet(?)",
                [str(ent_path)],
            ).fetchall()
        ]
        edges_raw = con.execute(
            "SELECT source, target, weight FROM read_parquet(?)",
            [str(rel_path)],
        ).fetchall()

    titles = {n["title"] for n in nodes}
    edges = [
        {"source": source, "target": target, "weight": float(weight)}
        for source, target, weight in edges_raw
        if source in titles and target in titles
    ]
    return {"level": int(chosen), "levels": levels, "nodes": nodes, "edges": edges}


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
