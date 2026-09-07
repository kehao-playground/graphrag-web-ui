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

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


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
            "human_readable_id",
            "title",
            "type",
            "frequency",
            "degree",
        ),
        keyword_fields=("title", "type", "description"),
        type_filter=True,
        community_filter=True,
    ),
    "relationships": TableSpec(
        name="relationships",
        list_columns=(
            "human_readable_id",
            "source",
            "target",
            "weight",
            "combined_degree",
        ),
        keyword_fields=("source", "target", "description"),
        type_filter=False,
        community_filter=False,
    ),
    "communities": TableSpec(
        name="communities",
        list_columns=(
            "human_readable_id",
            "community",
            "level",
            "parent",
            "size",
            "title",
        ),
        keyword_fields=("title",),
        type_filter=False,
        community_filter=True,
    ),
    "community_reports": TableSpec(
        name="community_reports",
        list_columns=(
            "human_readable_id",
            "community",
            "level",
            "rank",
            "title",
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


# graphrag_input/structured_file_reader.py:48-53 appends " (N)" to a
# structured file's title when the file yields more than one row. Anchored
# at the end and requiring at least one digit, so "report (1).csv" - a real
# filename - is never treated as a suffixed title.
_ROW_SUFFIX_RE = re.compile(r" \(\d+\)$")


def title_column_configured(settings_data: Any) -> bool:
    """Rule 1 of the recovery rule (spec 6.3): with input.title_column set,
    a title is arbitrary row data and no filename can be attributed to it.

    We never write title_column ourselves (adapters/workspace.py sets only
    input.type and input.file_pattern), but SettingsPanel lets a user
    hand-edit settings.yaml, so the case is reachable and is detected rather
    than assumed away.
    """
    if not isinstance(settings_data, dict):
        return False
    section = settings_data.get("input")
    if not isinstance(section, dict):
        return False
    return bool(section.get("title_column"))


def recover_filename(title: str, candidates: frozenset[str]) -> str | None:
    """Map one documents.title back to a filename, or None.

    Exact match is tried FIRST so a single-row file genuinely named
    "report (1).csv" is not stripped down to a name that does not exist.
    """
    if title in candidates:
        return title
    stripped = _ROW_SUFFIX_RE.sub("", title)
    if stripped != title and stripped in candidates:
        return stripped
    return None


def recover_filenames(titles: Iterable[str], candidates: frozenset[str]) -> frozenset[str]:
    """Filenames attributable to a set of titles; unmatched titles vanish."""
    out = {recover_filename(t, candidates) for t in titles}
    return frozenset(n for n in out if n is not None)
