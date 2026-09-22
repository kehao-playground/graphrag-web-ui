"""GraphRAG search adapter — the ONLY import site of ``graphrag`` in the
codebase (AGENTS.md import rule). Wraps ``graphrag.api.query`` search and
streaming functions with per-mode frame wiring, and re-exports
``load_config`` behind a stable error type. Nothing is caught here: the
query service owns error mapping (probe 2026-08-22 verified signatures
against graphrag 3.1.0)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import yaml

from graphrag_ui.adapters.workspace_env import read_workspace_env, substitute_placeholders
from graphrag_ui.domain.settings_confinement import (
    FILE_STORAGE_SECTIONS,
    PROMPT_FIELDS,
    confinement_violations,
)

# litellm (pulled in by graphrag) runs load_dotenv() AT IMPORT TIME, which
# walks up from the CWD and merges the nearest .env into os.environ — in
# dev that silently overrides app settings defaults (repo-root .env's
# PROJECT_QUOTA_MB once broke the files quota mid-suite). Snapshot/restore
# the environment around the import so the side effect stays local. Query-
# time loading never touches os.environ either: load_config below reads the
# workspace .env into a per-call dict (R2-02).
_environ_before_import = os.environ.copy()
# Same litellm noise as index_runner (botocore pre-load warnings on import);
# the handler level is read from LITELLM_LOG at import time, so default it
# before graphrag pulls litellm in. Restored below — the API process env is
# untouched; only the already-built litellm handler keeps the level.
os.environ.setdefault("LITELLM_LOG", "ERROR")
try:
    from graphrag.api.query import (
        basic_search,
        basic_search_streaming,
        drift_search,
        drift_search_streaming,
        global_search,
        global_search_streaming,
        local_search,
        local_search_streaming,
    )

    # graphrag.config.load_config(root) is NOT used: it substitutes `${VAR}`
    # from os.environ (R2-01), load_dotenv()s the workspace .env into the
    # process (R2-02) and os.chdir()s into the workspace (R2-06). The
    # graphrag_common loader underneath takes all three as flags plus a
    # parser hook, which is where the workspace-only substitution goes.
    from graphrag.config.models.graph_rag_config import GraphRagConfig
    from graphrag_common.config import load_config as _graphrag_common_load_config
finally:
    os.environ.clear()
    os.environ.update(_environ_before_import)
    del _environ_before_import

logger = logging.getLogger(__name__)

# graphrag CLI's default `--community-level` (2) — what our index jobs build
# with, since index_runner never overrides it.
DEFAULT_COMMUNITY_LEVEL = 2

# Frame names handed to graphrag per method (plan Global Constraints map).
_LOCAL_TABLES = ("entities", "communities", "community_reports", "text_units", "relationships")
_GLOBAL_TABLES = ("entities", "communities", "community_reports")


class ConfigLoadError(RuntimeError):
    """graphrag load_config failed (bad settings.yaml / template / workspace .env)."""


# settings.yaml fields graphrag reads as filesystem paths and resolves against
# the process cwd (GraphRagConfig validators call Path(x).resolve(); prompts
# and FileStorage open them relative to cwd). With set_cwd=False the API
# process stays where it is, so these are anchored to the workspace root
# here. Storage sections only when their backend is the local filesystem —
# for blob/cosmosdb base_dir is a container path. The field lists live in
# the domain module that also decides confinement (R2-03), so the anchor
# and the check can never disagree about which fields are paths.
_FILE_STORAGE_SECTIONS = FILE_STORAGE_SECTIONS
_PROMPT_FIELDS = PROMPT_FIELDS


def _anchor(section: Any, field: str, root: Path) -> None:
    value = section.get(field) if isinstance(section, dict) else None
    if isinstance(value, str) and value and not Path(value).is_absolute():
        section[field] = str(root / value)


def _section(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    node: Any = data
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _anchor_relative_paths(data: dict[str, Any], root: Path) -> dict[str, Any]:
    # Confinement before anchoring: an escaping path must fail the load,
    # never be anchored and opened (R2-03).
    escapes = confinement_violations(data)
    if escapes:
        raise ValueError(f"settings point outside the workspace: {', '.join(escapes)}")
    for keys in _FILE_STORAGE_SECTIONS:
        section = _section(data, keys)
        if isinstance(section, dict) and section.get("type", "file") == "file":
            _anchor(section, "base_dir", root)
    vector_store = data.get("vector_store")
    if isinstance(vector_store, dict) and vector_store.get("type", "lancedb") == "lancedb":
        _anchor(vector_store, "db_uri", root)
    for section_name, field in _PROMPT_FIELDS:
        _anchor(data.get(section_name), field, root)
    return data


def load_config(root: Path):
    """GraphRagConfig for the workspace at `root`, loaded inside the trust
    boundary: `${VAR}` substituted from the workspace .env alone, nothing
    merged into os.environ, no chdir, relative paths anchored to `root`.
    Every failure (missing placeholder, YAML, pydantic) surfaces as one type.
    """
    root = root.resolve()  # anchored paths must not depend on the process cwd
    env = read_workspace_env(root)

    def _parse(text: str) -> dict[str, Any]:
        try:
            data = yaml.safe_load(substitute_placeholders(text, env))
        except KeyError as exc:
            raise ValueError(f"placeholder not in workspace .env: {exc}") from exc
        if not isinstance(data, dict):
            raise TypeError("settings.yaml is not a mapping")
        return _anchor_relative_paths(data, root)

    try:
        return _graphrag_common_load_config(
            config_initializer=GraphRagConfig,
            config_path=root,
            set_cwd=False,
            parse_env_vars=False,
            config_parser=_parse,
        )
    except Exception as exc:  # ValueError/Template/YAML/pydantic — single stable wrap
        raise ConfigLoadError(str(exc)) from exc


class SearchAdapter(Protocol):
    """Seam for tests (and Task 4 streaming): search + stream callables."""

    async def search(
        self,
        method: str,
        config: Any,
        frames: dict[str, pd.DataFrame],
        query: str,
        response_type: str,
    ) -> tuple[str, dict[str, pd.DataFrame]]: ...

    def stream(
        self,
        method: str,
        config: Any,
        frames: dict[str, pd.DataFrame],
        query: str,
        response_type: str,
    ) -> AsyncIterator[str]: ...


class GraphragSearchAdapter:
    """Calls graphrag.api search functions; raises through on any failure."""

    async def search(
        self,
        method: str,
        config: Any,
        frames: dict[str, pd.DataFrame],
        query: str,
        response_type: str,
    ) -> tuple[str, dict[str, pd.DataFrame]]:
        fn = _SEARCH_FNS.get(method)
        if fn is None:
            raise ValueError(f"unknown query method: {method!r}")
        result, context = await fn(
            config=config,
            query=query,
            response_type=response_type,
            **_frames_kwargs(method, config, frames),
        )
        # answer is str in practice; dict/list occur with JSON-mode response types
        answer = result if isinstance(result, str) else str(result)
        if isinstance(context, dict):
            context_frames = context
        else:
            logger.warning(
                "graphrag %s_search returned %s context (not dict); dropping it",
                method,
                type(context).__name__,
            )
            context_frames = {}
        return answer, context_frames

    def stream(
        self,
        method: str,
        config: Any,
        frames: dict[str, pd.DataFrame],
        query: str,
        response_type: str,
    ) -> AsyncIterator[str]:
        fn = _STREAM_FNS.get(method)
        if fn is None:
            raise ValueError(f"unknown query method: {method!r}")
        # *_streaming are sync calls returning AsyncGenerator[str, None]
        return fn(
            config=config,
            query=query,
            response_type=response_type,
            **_frames_kwargs(method, config, frames),
        )


def _frames_kwargs(method: str, config: Any, frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Per-mode required-arg wiring from the loaded frames dict."""
    kwargs: dict[str, Any] = {}
    if method == "basic":
        kwargs["text_units"] = frames["text_units"]
    else:
        tables = _GLOBAL_TABLES if method == "global" else _LOCAL_TABLES
        kwargs = {name: frames[name] for name in tables}
        # graphrag CLI default --community-level 2 (index_runner never overrides)
        kwargs["community_level"] = (
            getattr(config, "community_level", None) or DEFAULT_COMMUNITY_LEVEL
        )
        if method == "global":
            kwargs["dynamic_community_selection"] = False
        elif method == "local":
            # covariates optional (spec §6.4): skip loading, graphrag handles None
            kwargs["covariates"] = None
    return kwargs


# Any-valued on purpose: graphrag ships no type information, and these come
# through the env-shielded import below, so a checker can only see "unknown".
# The keys are the closed method set the callers validate against.
_SEARCH_FNS: dict[str, Any] = {
    "basic": basic_search,
    "local": local_search,
    "drift": drift_search,
    "global": global_search,
}

_STREAM_FNS: dict[str, Any] = {
    "basic": basic_search_streaming,
    "local": local_search_streaming,
    "drift": drift_search_streaming,
    "global": global_search_streaming,
}
