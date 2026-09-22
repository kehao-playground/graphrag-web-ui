"""Workspace confinement of settings.yaml (R2-03).

graphrag resolves every file-storage `base_dir`, the lancedb `db_uri` and
every prompt path with `Path(...).resolve()` / `read_text()` and no root
check. The index subprocess runs as the uid that owns the whole workspaces
volume, so a settings.yaml pointing one of them at `../<other project>`
reads (input, prompts), overwrites (output, update_output) or writes into
(reporting, cache) another project's workspace, and `blob`/`cosmosdb`
backends ship data off-host. These rules keep every such path a relative
one that stays inside the workspace and every backend a local one.

Pure domain layer: string/path math on an already-parsed (and already
$-substituted) settings document; no I/O, no external imports.
"""

import posixpath
from collections.abc import Iterable
from typing import Any

# Sections whose `base_dir` graphrag opens as a local directory when their
# `type` is file (the default when the key is absent). The same tuple
# anchors relative paths in adapters/graphrag_search.py.
FILE_STORAGE_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("input_storage",),
    ("output_storage",),
    ("update_output_storage",),
    ("reporting",),
    ("cache", "storage"),
)
# graphrag_storage.StorageType is file/memory/blob/cosmosdb and the models
# are extra="allow" for custom backends: allowlist the local ones.
_STORAGE_TYPES = frozenset({"file", "memory"})
# graphrag.config.enums.ReportingType is file/blob only.
_REPORTING_TYPES = frozenset({"file"})
_VECTOR_STORE_TYPES = frozenset({"lancedb"})
# (section, field) pairs graphrag reads with Path(field).read_text().
PROMPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("extract_graph", "prompt"),
    ("summarize_descriptions", "prompt"),
    ("extract_claims", "prompt"),
    ("community_reports", "graph_prompt"),
    ("community_reports", "text_prompt"),
    ("local_search", "prompt"),
    ("global_search", "map_prompt"),
    ("global_search", "reduce_prompt"),
    ("global_search", "knowledge_prompt"),
    ("drift_search", "prompt"),
    ("drift_search", "reduce_prompt"),
    ("basic_search", "prompt"),
)

# The regexes adapters/workspace.py writes per input_file_type at creation.
# Not pinned afterwards: narrowing the pattern is a legitimate edit, and with
# input_storage confined it can only select among the project's own files.
INPUT_FILE_PATTERNS: dict[str, str] = {
    "text": r".*\.(txt|md)$",
    "csv": r".*\.csv$",
    "json": r".*\.json$",
}


def is_confined(value: str) -> bool:
    """A path graphrag would keep inside the workspace: relative, and still
    inside after lexical normalization (so `a/../b` passes and `a/../../b`
    does not). Symlinks are not a concern — nothing lets a project member
    create one inside a workspace."""
    if value.startswith(("/", "\\")):
        return False
    normalized = posixpath.normpath(value)
    return normalized != ".." and not normalized.startswith("../")


def _section(data: Any, keys: Iterable[str]) -> Any:
    node = data
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _path_violation(section: Any, field: str) -> bool:
    value = section.get(field) if isinstance(section, dict) else None
    return isinstance(value, str) and bool(value) and not is_confined(value)


def confinement_violations(data: Any) -> list[str]:
    """Dotted names of the fields that would take graphrag outside the
    workspace, in document order; empty when the settings are confined.
    Absent sections and fields are graphrag defaults, all of which are
    relative paths under the root."""
    out: list[str] = []
    for keys in FILE_STORAGE_SECTIONS:
        section = _section(data, keys)
        if not isinstance(section, dict):
            continue
        prefix = ".".join(keys)
        kind = section.get("type", "file")
        allowed = _REPORTING_TYPES if keys == ("reporting",) else _STORAGE_TYPES
        if kind not in allowed:
            out.append(f"{prefix}.type")
        elif kind == "file" and _path_violation(section, "base_dir"):
            out.append(f"{prefix}.base_dir")
    vector_store = _section(data, ("vector_store",))
    if isinstance(vector_store, dict):
        if vector_store.get("type", "lancedb") not in _VECTOR_STORE_TYPES:
            out.append("vector_store.type")
        elif _path_violation(vector_store, "db_uri"):
            out.append("vector_store.db_uri")
    for section_name, field in PROMPT_FIELDS:
        if _path_violation(_section(data, (section_name,)), field):
            out.append(f"{section_name}.{field}")
    return out


def input_pin_violations(data: Any, input_file_type: str) -> list[str]:
    """`input.type` is what adapters/workspace.py wrote at creation — the
    input format is locked then (spec §6.5 errata, decision D6) — so when
    present it must still say so. An absent key is left to graphrag's
    default."""
    section = _section(data, ("input",))
    if isinstance(section, dict) and "type" in section and section["type"] != input_file_type:
        return ["input.type"]
    return []
