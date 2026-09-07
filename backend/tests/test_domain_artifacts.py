"""Registry invariants (spec §6.1/§13): six tables, list projections exclude
big columns, filter flags only where the parquet schema supports them."""

from graphrag_ui.domain.artifacts import (
    TABLES,
    recover_filename,
    recover_filenames,
    table_spec,
    title_column_configured,
)


def test_six_tables_registered():
    assert set(TABLES) == {
        "entities",
        "relationships",
        "communities",
        "community_reports",
        "text_units",
        "documents",
    }


def test_entities_filters_and_documents_projection():
    ent = TABLES["entities"]
    assert ent.type_filter and ent.community_filter
    assert "description" in ent.keyword_fields and "description" not in ent.list_columns
    docs = TABLES["documents"]
    assert "text" not in docs.list_columns and "raw_data" not in docs.list_columns
    reports = TABLES["community_reports"]
    assert "full_content" not in reports.list_columns and "findings" not in reports.list_columns


def test_community_filter_only_where_column_exists():
    for name in ("relationships", "text_units", "documents"):
        assert not TABLES[name].community_filter and not TABLES[name].type_filter


def test_table_spec_unknown_returns_none():
    assert table_spec("graph") is None and table_spec("nope") is None


CANDIDATES = frozenset({"report.csv", "report (1).csv", "notes.md", "old.md"})


def test_exact_match_wins_over_suffix_stripping():
    """A single-row 'report (1).csv' must not be mangled into 'report'.
    Rule 2 is checked before rule 3 precisely for this case (spec 6.3)."""
    assert recover_filename("report (1).csv", CANDIDATES) == "report (1).csv"


def test_multi_row_structured_title_strips_the_row_suffix():
    assert recover_filename("report.csv (0)", CANDIDATES) == "report.csv"
    assert recover_filename("report.csv (12)", CANDIDATES) == "report.csv"


def test_plain_text_title_is_the_filename():
    assert recover_filename("notes.md", CANDIDATES) == "notes.md"


def test_unmatched_title_maps_to_nothing():
    assert recover_filename("Q3 revenue", CANDIDATES) is None
    assert recover_filename("absent.csv (0)", CANDIDATES) is None


def test_recover_filenames_drops_unmatched_and_dedupes():
    titles = ["notes.md", "report.csv (0)", "report.csv (1)", "Q3 revenue"]
    assert recover_filenames(titles, CANDIDATES) == frozenset({"notes.md", "report.csv"})


def test_candidates_may_include_names_no_longer_on_disk():
    """old.md was deleted from input/ but is still in the index after an
    update; a citation into it must still resolve (spec 6.3)."""
    assert recover_filename("old.md", CANDIDATES) == "old.md"


def test_title_column_configured_detects_rule_1():
    assert title_column_configured({"input": {"title_column": "name"}}) is True
    assert title_column_configured({"input": {"type": "csv"}}) is False
    assert title_column_configured({}) is False
    assert title_column_configured({"input": None}) is False
    # An empty string is not a configured column.
    assert title_column_configured({"input": {"title_column": ""}}) is False
