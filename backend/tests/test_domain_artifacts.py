"""Registry invariants (spec §6.1/§13): six tables, list projections exclude
big columns, filter flags only where the parquet schema supports them."""
from graphrag_ui.domain.artifacts import TABLES, table_spec


def test_six_tables_registered():
    assert set(TABLES) == {
        "entities", "relationships", "communities",
        "community_reports", "text_units", "documents",
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
