from graphrag_ui.domain.citations import build_citations, parse_markers


def test_parse_single_probe_marker():
    text = "...machine execution [Data: Sources (2)]."
    assert parse_markers(text) == [("Sources", [2])]


def test_parse_multi_label_group():
    text = "[Data: Entities (12, 34); Reports (5)]"
    assert parse_markers(text) == [("Entities", [12, 34]), ("Reports", [5])]


def test_parse_multiple_markers_preserve_order():
    text = "first [Data: Reports (1)] middle [Data: Sources (7, 8)] last"
    assert parse_markers(text) == [("Reports", [1]), ("Sources", [7, 8])]


def test_parse_malformed_group_skipped_silently():
    assert parse_markers("[Data: Entities (abc)]") == []
    assert parse_markers("[Data: ; Sources (1)]") == [("Sources", [1])]


def test_parse_ids_with_spaces():
    assert parse_markers("[Data: Sources (1, 2, 3)]") == [("Sources", [1, 2, 3])]


def test_parse_empty_text():
    assert parse_markers("") == []
    assert parse_markers("no markers here") == []


def test_parse_dedupes_repeated_marker_keeps_first_position():
    text = "a [Data: Sources (2)] b [Data: Sources (2)] c [Data: Reports (5)]"
    assert parse_markers(text) == [("Sources", [2]), ("Reports", [5])]


def test_build_citations_joins_text_from_key_dict():
    text = "answer cites [Data: Sources (2)]."
    frames = {"sources": {2: "source body"}}
    assert build_citations(text, frames) == [
        {
            "label": "Sources",
            "ids": [2],
            "entries": [{"id": 2, "text": "source body"}],
        }
    ]


def test_build_citations_normalizes_labels():
    text = "[Data: Text Units (1); Relations (9)]"
    frames = {"text_units": {1: "unit text"}, "relationships": {9: "rel text"}}
    citations = build_citations(text, frames)
    assert [c["entries"][0]["text"] for c in citations] == ["unit text", "rel text"]


def test_build_citations_missing_key_keeps_ids_empty_entries():
    citations = build_citations("[Data: Sources (2)]", {})
    assert citations == [{"label": "Sources", "ids": [2], "entries": []}]


def test_build_citations_missing_id_text_none():
    citations = build_citations("[Data: Sources (2)]", {"sources": {99: "other"}})
    assert citations[0]["entries"] == [{"id": 2, "text": None}]


def test_build_citations_never_raises():
    assert build_citations("", {"sources": {1: "x"}}) == []
    assert build_citations("[Data: Sources (1)]", {}) != []
    assert parse_markers("[Data:]") == []
    assert parse_markers("[Data: Sources ()]") == []
