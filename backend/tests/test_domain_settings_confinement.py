"""Workspace confinement of settings.yaml (R2-03): the pure rules that
decide whether a parsed settings document would take graphrag outside the
project workspace — storage/reporting/cache base_dirs, the lancedb db_uri,
prompt files — or switch its input away from what the app owns.
"""

import pytest

from graphrag_ui.domain.settings_confinement import (
    confinement_violations,
    input_pin_violations,
)

# What `graphrag init` + adapters/workspace.py write (after $-substitution).
INIT_LIKE = {
    "input": {"type": "text", "file_pattern": r".*\.(txt|md)$"},
    "input_storage": {"type": "file", "base_dir": "input"},
    "output_storage": {"type": "file", "base_dir": "output"},
    "update_output_storage": {"type": "file", "base_dir": "update_output"},
    "reporting": {"type": "file", "base_dir": "logs"},
    "cache": {"type": "json", "storage": {"type": "file", "base_dir": "cache"}},
    "vector_store": {"type": "lancedb", "db_uri": "output/lancedb"},
    "extract_graph": {"prompt": "prompts/extract_graph.txt"},
    "community_reports": {"graph_prompt": "prompts/a.txt", "text_prompt": "prompts/b.txt"},
}


def _with(section: str, **fields) -> dict:
    data = {k: dict(v) for k, v in INIT_LIKE.items()}
    data[section] = {**data[section], **fields}
    return data


def test_the_init_layout_is_confined():
    assert confinement_violations(INIT_LIKE) == []


def test_missing_sections_default_to_confined_file_storage():
    assert confinement_violations({}) == []
    assert confinement_violations({"input_storage": {}}) == []
    assert confinement_violations(None) == []
    assert confinement_violations("not a mapping") == []


@pytest.mark.parametrize(
    "value",
    ["/data/workspaces", "../other-project/input", "input/../../x", "..", "./../x", "a/./../../b"],
)
def test_a_base_dir_leaving_the_workspace_is_a_violation(value):
    assert confinement_violations(_with("input_storage", base_dir=value)) == [
        "input_storage.base_dir"
    ]


@pytest.mark.parametrize("value", ["input", "./input", "nested/dir", "a/../b", "."])
def test_relative_paths_inside_the_workspace_pass(value):
    assert confinement_violations(_with("output_storage", base_dir=value)) == []


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("input_storage", "input_storage.base_dir"),
        ("output_storage", "output_storage.base_dir"),
        ("update_output_storage", "update_output_storage.base_dir"),
        ("reporting", "reporting.base_dir"),
    ],
)
def test_every_file_storage_section_is_checked(section, field):
    assert confinement_violations(_with(section, base_dir="../x")) == [field]


def test_the_nested_cache_storage_is_checked():
    data = _with("cache", storage={"type": "file", "base_dir": "/tmp/cache"})
    assert confinement_violations(data) == ["cache.storage.base_dir"]


@pytest.mark.parametrize("kind", ["blob", "cosmosdb", "custom.my.Storage"])
def test_remote_and_unknown_storage_backends_are_violations(kind):
    assert confinement_violations(_with("output_storage", type=kind)) == ["output_storage.type"]
    assert confinement_violations(_with("reporting", type=kind)) == ["reporting.type"]


def test_memory_storage_is_allowed_and_its_base_dir_ignored():
    assert confinement_violations(_with("cache", storage={"type": "memory"})) == []
    data = _with("output_storage", type="memory", base_dir="/anywhere")
    assert confinement_violations(data) == []


def test_reporting_has_no_memory_backend():
    assert confinement_violations(_with("reporting", type="memory")) == ["reporting.type"]


def test_vector_store_must_be_lancedb_inside_the_workspace():
    assert confinement_violations(_with("vector_store", db_uri="/data/other/lancedb")) == [
        "vector_store.db_uri"
    ]
    assert confinement_violations(_with("vector_store", type="azure_ai_search")) == [
        "vector_store.type"
    ]
    assert confinement_violations(_with("vector_store", type="cosmosdb")) == ["vector_store.type"]


def test_prompt_files_are_confined_too():
    """graphrag reads every prompt path with Path(...).read_text(): pointed
    at another workspace's .env it would paste that project's secrets into
    the extraction prompt."""
    data = _with("extract_graph", prompt="../other/.env")
    assert confinement_violations(data) == ["extract_graph.prompt"]
    data = _with("community_reports", text_prompt="/etc/passwd")
    assert confinement_violations(data) == ["community_reports.text_prompt"]


def test_violations_are_reported_in_document_order_and_all_at_once():
    data = _with("input_storage", base_dir="../a")
    data["reporting"] = {"type": "blob"}
    data["vector_store"] = {"type": "lancedb", "db_uri": "/x"}
    # a disallowed backend reports its type only: base_dir means nothing then
    assert confinement_violations(data) == [
        "input_storage.base_dir",
        "reporting.type",
        "vector_store.db_uri",
    ]


def test_input_pin_accepts_what_the_app_wrote_and_an_absent_section():
    assert input_pin_violations(INIT_LIKE, "text") == []
    assert input_pin_violations({}, "csv") == []
    assert input_pin_violations({"input": {"type": "csv"}}, "csv") == []


def test_input_pin_refuses_a_changed_type_but_leaves_the_pattern_free():
    """Narrowing file_pattern is an ordinary edit (the editor tests do it);
    inside a confined input_storage it only selects among the project's
    own uploads, and what it deselects the listing reports as skipped."""
    assert input_pin_violations(_with("input", type="csv"), "text") == ["input.type"]
    assert input_pin_violations(INIT_LIKE, "json") == ["input.type"]
    assert input_pin_violations(_with("input", file_pattern=".*"), "text") == []
