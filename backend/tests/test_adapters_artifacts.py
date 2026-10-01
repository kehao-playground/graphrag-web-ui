from contextlib import contextmanager

import pandas as pd
import pytest

from graphrag_ui.adapters import artifacts as artifacts_module
from graphrag_ui.adapters.artifacts import (
    ArtifactsNotIndexedError,
    get_row,
    graph,
    list_rows,
    resolve_document_titles,
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


@pytest.fixture
def docs_ws(tmp_path):
    """Workspace whose output/ holds only documents.parquet."""

    def make(documents):
        out = tmp_path / "output"
        out.mkdir(exist_ok=True)
        ids, titles = zip(*documents)
        pd.DataFrame({"id": list(ids), "title": list(titles)}).to_parquet(out / "documents.parquet")
        return tmp_path

    return make


def _explode(message):
    def boom(*args, **kwargs):
        raise AssertionError(message)

    return boom


def test_resolve_document_titles_returns_only_requested_ids(docs_ws):
    root = docs_ws(documents=[("d1", "a.md"), ("d2", "b.md"), ("d3", "c.md")])
    assert resolve_document_titles(root, {"d1", "d3"}) == {"d1": "a.md", "d3": "c.md"}


def test_unknown_ids_are_absent_not_none(docs_ws):
    root = docs_ws(documents=[("d1", "a.md")])
    assert resolve_document_titles(root, {"d1", "ghost"}) == {"d1": "a.md"}


def test_empty_id_set_reads_nothing(docs_ws, monkeypatch):
    """A question that cites no sources must not open duckdb at all."""
    root = docs_ws(documents=[("d1", "a.md")])
    monkeypatch.setattr(artifacts_module, "_cursor", _explode("no read for an empty id set"))
    assert resolve_document_titles(root, set()) == {}


def test_missing_parquet_yields_an_empty_mapping(tmp_path):
    """Best-effort: a missing documents.parquet renders citations unlinked,
    it does not fail the answer."""
    assert resolve_document_titles(tmp_path, {"d1"}) == {}


def test_one_read_for_many_ids(docs_ws, monkeypatch):
    root = docs_ws(documents=[(f"d{i}", f"f{i}.md") for i in range(50)])
    spy = _spy(monkeypatch)
    out = resolve_document_titles(root, {f"d{i}" for i in range(50)})
    assert len(out) == 50 and len(spy.statements) == 1


class _Spy:
    """Records every statement the adapter runs and how many rows it pulled
    into Python."""

    def __init__(self):
        self.statements: list[str] = []
        self.rows_fetched = 0


def _spy(monkeypatch) -> _Spy:
    spy = _Spy()
    real = artifacts_module._cursor

    class Recording:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=None):
            spy.statements.append(sql)
            self._cur.execute(sql, params)
            return self

        @property
        def description(self):
            return self._cur.description

        def fetchall(self):
            rows = self._cur.fetchall()
            spy.rows_fetched += len(rows)
            return rows

        def fetchone(self):
            row = self._cur.fetchone()
            spy.rows_fetched += row is not None
            return row

    @contextmanager
    def recording():
        with real() as cur:
            yield Recording(cur)

    monkeypatch.setattr(artifacts_module, "_cursor", recording)
    return spy


@pytest.fixture
def big_ws(tmp_path):
    """200 entities in a chain plus 300 relationships: big enough that a
    full read is distinguishable from a capped one."""
    out = tmp_path / "output"
    out.mkdir()
    n = 200
    titles = [f"E{i}" for i in range(n)]
    pd.DataFrame(
        {
            "id": [f"e{i}" for i in range(n)],
            "human_readable_id": list(range(n)),
            "title": titles,
            "type": ["T"] * n,
            "frequency": [1] * n,
            "degree": list(range(n)),  # E199 is the biggest hub
            "description": [""] * n,
        }
    ).to_parquet(out / "entities.parquet")
    pd.DataFrame(
        {
            "id": [f"r{i}" for i in range(300)],
            "human_readable_id": list(range(300)),
            "source": [titles[i % n] for i in range(300)],
            "target": [titles[(i + 1) % n] for i in range(300)],
            "weight": [1.0] * 300,
        }
    ).to_parquet(out / "relationships.parquet")
    pd.DataFrame(
        {
            "id": ["c0"],
            "human_readable_id": [0],
            "community": [7],
            "level": [0],
            "entity_ids": [[f"e{i}" for i in range(n)]],
        }
    ).to_parquet(out / "communities.parquet")
    return tmp_path


def test_a_capped_graph_pulls_only_the_kept_rows_into_python(big_ws, monkeypatch):
    """R1-78: the cap, the edge filter and the community lookup run in
    duckdb; Python sees the kept nodes and their edges, not the corpus."""
    spy = _spy(monkeypatch)
    data = graph(big_ws, node_limit=5)
    assert [n["title"] for n in data["nodes"]] == ["E199", "E198", "E197", "E196", "E195"]
    assert all(n["community"] == 7 for n in data["nodes"])
    assert {(e["source"], e["target"]) for e in data["edges"]} == {
        ("E195", "E196"),
        ("E196", "E197"),
        ("E197", "E198"),
        ("E198", "E199"),
    }
    assert data["truncated"] is True
    # levels (1) + count (1) + nodes (5) + edges (a handful), never ~500
    assert spy.rows_fetched < 20


def test_an_uncapped_graph_still_returns_everything(big_ws):
    data = graph(big_ws, node_limit=None)
    assert len(data["nodes"]) == 200 and data["truncated"] is False
    assert len(data["edges"]) == 300


def test_list_rows_runs_one_statement_per_page(ws, monkeypatch):
    """R1-102: the total rides the page query (COUNT(*) OVER ())."""
    spy = _spy(monkeypatch)
    rows, total = list_rows(ws, "entities", limit=2, offset=0)
    assert len(rows) == 2 and total == 3
    assert len(spy.statements) == 1


def test_list_rows_total_survives_an_offset_past_the_end(ws):
    rows, total = list_rows(ws, "entities", limit=2, offset=10)
    assert rows == [] and total == 3


def test_reads_share_one_duckdb_database(ws, monkeypatch):
    """R1-102: no fresh in-memory database per call."""
    list_rows(ws, "entities", limit=1, offset=0)  # warm the shared connection
    monkeypatch.setattr(artifacts_module.duckdb, "connect", _explode("connect per call"))
    list_rows(ws, "entities", limit=1, offset=0)
    get_row(ws, "entities", 1)
    graph(ws)
    resolve_document_titles(ws, {"x"})
