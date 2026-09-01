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

# litellm (pulled in by graphrag) runs load_dotenv() AT IMPORT TIME, which
# walks up from the CWD and merges the nearest .env into os.environ — in
# dev that silently overrides app settings defaults (repo-root .env's
# PROJECT_QUOTA_MB once broke the files quota mid-suite). Snapshot/restore
# the environment around the import so the side effect stays local; the
# intentional workspace-.env loading at query time (load_config(root)) is
# untouched.
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

    # NOTE: import the FUNCTION from the submodule — `from graphrag.config
    # import load_config` binds the submodule (import system shadows the
    # re-export), which is not callable.
    from graphrag.config.load_config import load_config as _graphrag_load_config
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


def load_config(root: Path):
    """Wrap graphrag.config.load_config so config failures surface as one type."""
    try:
        return _graphrag_load_config(root)
    except Exception as exc:  # ValueError/Template/YAML/… — single stable wrap
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


_SEARCH_FNS = {
    "basic": basic_search,
    "local": local_search,
    "drift": drift_search,
    "global": global_search,
}

_STREAM_FNS = {
    "basic": basic_search_streaming,
    "local": local_search_streaming,
    "drift": drift_search_streaming,
    "global": global_search_streaming,
}
