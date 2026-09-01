import pandas as pd
import pytest

from graphrag_ui.adapters.artifacts import (
    ArtifactsNotIndexedError,
    get_row,
    graph,
    list_rows,
)


@pytest.fixture
def ws(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    pd.DataFrame(
        {
            "id": ["e1", "e2", "e3"],
            "human_readable_id": [1, 2, 3],
            "title": ["Alan Turing", "Analytical Engine", "Ada Lovelace"],
            "type": ["PERSON", "ARTIFACT", "PERSON"],
            "text_unit_ids": [["a"], ["b"], ["c"]],
            "frequency": [3, 2, 2],
            "degree": [2, 1, 0],
            "description": ["computed", "machine", "first programmer"],
        }
    ).to_parquet(out / "entities.parquet")
    pd.DataFrame(
        {
            "id": ["r1", "r2"],
            "human_readable_id": [1, 2],
            "source": ["Alan Turing", "Ada Lovelace"],
            "target": ["Ada Lovelace", "Ghost Entity"],
            "weight": [4.0, 1.0],
            "combined_degree": [2, 0],
            "text_unit_ids": [["a"], []],
            "description": ["correspondence", "dangling"],
        }
    ).to_parquet(out / "relationships.parquet")
    pd.DataFrame(
        {
            "id": ["c1", "c2"],
            "human_readable_id": [0, 1],
            "community": [0, 1],
            "level": [0, 1],
            "parent": [-1, 0],
            "children": [[1], []],
            "title": ["C0", "C1"],
            "entity_ids": [["e1", "e2"], ["e3"]],
            "relationship_ids": [["r1"], []],
            "text_unit_ids": [[], []],
            "period": ["2026-08-22"] * 2,
            "size": [2, 1],
        }
    ).to_parquet(out / "communities.parquet")
    return tmp_path


def test_list_rows_pagination_and_keyword(ws):
    rows, total = list_rows(ws, "entities", limit=2, offset=0, q="TURING")
    assert total == 1 and rows[0]["title"] == "Alan Turing"  # ILIKE case-insensitive
    rows, total = list_rows(ws, "entities", limit=2, offset=0)
    assert total == 3 and [r["human_readable_id"] for r in rows] == [1, 2]  # hrid order


def test_list_rows_type_and_community_filters(ws):
    _, total = list_rows(ws, "entities", limit=10, offset=0, type_filter="PERSON")
    assert total == 2
    # entity→community via communities(level=MAX) entity_ids (§13: entities lack the column)
    _, total = list_rows(ws, "entities", limit=10, offset=0, community=1)
    assert total == 1  # only e3 belongs to community 1 at level 1


def test_get_row_full_columns_and_missing(ws):
    row = get_row(ws, "entities", 1)
    assert row["description"] == "computed" and row["text_unit_ids"] == ["a"]
    assert get_row(ws, "entities", 99) is None


def test_graph_colors_via_max_level_and_drops_dangling_edges(ws):
    data = graph(ws)  # default level = MAX(level) = 1 → e3 in community 1
    assert data["levels"] == [0, 1] and data["level"] == 1
    nodes = {n["title"]: n for n in data["nodes"]}
    assert len(nodes) == 3  # ALL entities are nodes regardless of community
    assert nodes["Ada Lovelace"]["community"] == 1
    assert nodes["Alan Turing"]["community"] is None  # not in any level-1 community
    # edge r2 targets a title absent from entities → dropped; r1 survives
    assert [(e["source"], e["target"]) for e in data["edges"]] == [("Alan Turing", "Ada Lovelace")]


def test_graph_explicit_level(ws):
    data = graph(ws, level=0)  # level 0 community 0 owns e1, e2
    nodes = {n["title"]: n for n in data["nodes"]}
    assert nodes["Alan Turing"]["community"] == 0


def test_not_indexed(ws):
    (ws / "output" / "entities.parquet").unlink()
    with pytest.raises(ArtifactsNotIndexedError):
        list_rows(ws, "entities", limit=10, offset=0)
    with pytest.raises(ArtifactsNotIndexedError):
        graph(ws)


def test_graph_is_not_truncated_below_the_limit(ws):
    data = graph(ws, node_limit=10)
    assert data["truncated"] is False
    assert data["node_limit"] == 10
    assert len(data["nodes"]) == 3


def test_graph_caps_nodes_and_flags_truncation(ws):
    """Every entity and relationship used to be read into memory and returned
    in one response — fine on a demo corpus, a cliff on a real one."""
    data = graph(ws, node_limit=2)
    assert data["truncated"] is True
    assert data["node_limit"] == 2
    assert len(data["nodes"]) == 2


def test_graph_keeps_the_highest_degree_nodes_when_capped(ws):
    # Degree order is the useful order: the hubs are what a reader wants to
    # see, and dropping them first would leave an unreadable dust cloud.
    data = graph(ws, node_limit=2)
    assert {n["title"] for n in data["nodes"]} == {"Alan Turing", "Analytical Engine"}


def test_capped_graph_drops_edges_whose_endpoint_was_cut(ws):
    # r1 is Alan Turing -> Ada Lovelace; Ada is cut at limit 2, so the edge
    # must go with her rather than dangle into a node the client never got.
    data = graph(ws, node_limit=2)
    assert data["edges"] == []


def test_graph_level_choice_is_unaffected_by_the_cap(ws):
    data = graph(ws, level=0, node_limit=2)
    assert data["level"] == 0 and data["levels"] == [0, 1]
