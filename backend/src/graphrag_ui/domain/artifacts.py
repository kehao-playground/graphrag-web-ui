"""Artifact table registry for the Explore tab (spec §6.1/§13).

Column lists mirror the graphrag 3.1.0 parquet schemas probed on 2026-08-22
(§13) — they are empirical, not derived from a schema contract. List
projections deliberately exclude the big text/list columns (documents.text /
raw_data, community_reports.full_content / findings) so the browse endpoint
stays cheap; the detail endpoint returns full rows regardless.

Filter flags describe what the UI/API offers per table, not raw column
presence: `type_filter` requires a `type` column (entities only);
`community_filter` requires either a `community` column (communities,
community_reports) or a joinable community via communities.entity_ids at
MAX(level) — entities have no community column themselves (§13), the flag
still holds because the adapter resolves it through that join.

Pure domain layer: no I/O, no external imports.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    """Projection/filter metadata for one artifact parquet table."""

    name: str
    list_columns: tuple[str, ...]
    keyword_fields: tuple[str, ...]
    type_filter: bool
    community_filter: bool


TABLES: dict[str, TableSpec] = {
    "entities": TableSpec(
        name="entities",
        list_columns=(
            "human_readable_id", "title", "type", "frequency", "degree",
        ),
        keyword_fields=("title", "type", "description"),
        type_filter=True,
        community_filter=True,
    ),
    "relationships": TableSpec(
        name="relationships",
        list_columns=(
            "human_readable_id", "source", "target", "weight", "combined_degree",
        ),
        keyword_fields=("source", "target", "description"),
        type_filter=False,
        community_filter=False,
    ),
    "communities": TableSpec(
        name="communities",
        list_columns=(
            "human_readable_id", "community", "level", "parent", "size", "title",
        ),
        keyword_fields=("title",),
        type_filter=False,
        community_filter=True,
    ),
    "community_reports": TableSpec(
        name="community_reports",
        list_columns=(
            "human_readable_id", "community", "level", "rank", "title",
        ),
        keyword_fields=("title", "summary"),
        type_filter=False,
        community_filter=True,
    ),
    "text_units": TableSpec(
        name="text_units",
        list_columns=("human_readable_id", "n_tokens", "document_id"),
        keyword_fields=("text",),
        type_filter=False,
        community_filter=False,
    ),
    "documents": TableSpec(
        name="documents",
        list_columns=("human_readable_id", "title", "creation_date"),
        keyword_fields=("title",),
        type_filter=False,
        community_filter=False,
    ),
}


def table_spec(name: str) -> TableSpec | None:
    """Look up a table spec; None for unknown names (incl. 'graph')."""
    return TABLES.get(name)
