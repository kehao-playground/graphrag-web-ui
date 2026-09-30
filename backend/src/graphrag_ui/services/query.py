"""Query use case (spec §6.1/§6.4): rate limit → load config → load frames
from the per-project cache → search via the adapter → join citations from
the returned context frames → shape the response with timings.

Error contract (route maps, service never touches HTTP):
- QueryRateLimitedError / WorkspaceNotIndexedError re-raised as-is (429/409)
- ConfigLoadError → QueryError(code="config") (500)
- adapter or frame-load failure → QueryError with the exception tail kept
  SERVER-SIDE only (logger.exception); clients get a fixed message (502)
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from graphrag_ui.adapters.frame_cache import WorkspaceNotIndexedError, get_frame_cache, tables_for
from graphrag_ui.adapters.graphrag_search import GraphragSearchAdapter, load_config
from graphrag_ui.adapters.models import Project, User
from graphrag_ui.domain.citations import build_citations, cited_ids, frame_key
from graphrag_ui.services.citations import (
    CitationMemo,
    Generation,
    enrich_sources,
    read_generation,
)
from graphrag_ui.services.errors import INTERRUPTED_DETAIL, ServicePipelineError
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

# graphrag's documented default response format (same as the CLI).
DEFAULT_RESPONSE_TYPE = "multiple paragraphs"

# First present column wins when flattening a context frame to {id: text}.
_TEXT_COLUMNS = ("text", "title", "description", "name")


class QueryError(ServicePipelineError):
    """Query pipeline failure. code: "config" (500) | "search" (502).
    detail is for the server log only — routes return fixed messages."""


def _cited_texts(answer: str, frames: dict[str, pd.DataFrame]) -> dict[str, dict[int, str | None]]:
    """{canonical key: {id: text}} for the ids the answer's markers cite,
    for the pure citation builder.

    Every frame name maps once onto the domain's canonical key: graphrag
    names the text-units frame "units" or "sources" per mode while the
    cached parquet table is "text_units", and the reports frame "reports"
    while the table is "community_reports" (R1-89). The first frame on a
    key wins. Only cited rows are flattened, so an answer citing five text
    units costs five rows, not a copy of every table (R1-72)."""
    wanted = cited_ids(answer)
    texts: dict[str, dict[int, str | None]] = {}
    for name, df in frames.items():
        key = frame_key(name)
        if key in wanted and key not in texts:
            texts[key] = _frame_texts(df, wanted[key])
    return texts


def _frame_texts(df: pd.DataFrame, ids: set[int]) -> dict[int, str | None]:
    if "id" not in df.columns:
        return {}
    text_col = next((c for c in _TEXT_COLUMNS if c in df.columns), None)
    if text_col is None:
        return {}
    # graphrag 3.1.0 frames come in two shapes: cached parquet tables carry
    # id = SHA-512 hash string (index/workflows/create_base_text_units.py)
    # AND human_readable_id = 0-based int (create_final_text_units.py) —
    # answer markers cite that int (model short_id reads human_readable_id);
    # search-context frames instead put the same int straight into "id".
    # Key on human_readable_id when both columns exist, else on int(id);
    # non-int ids coerce to NaN and match nothing, so a hash id without
    # hrid resolves nothing instead of raising.
    id_col = "human_readable_id" if "human_readable_id" in df.columns else "id"
    keys = pd.to_numeric(df[id_col], errors="coerce")
    cited = keys.isin(ids)
    # NaN cells render as text: null (domain keeps None for missing text)
    return {
        int(entry_id): None if pd.isna(text) else str(text)
        for entry_id, text in zip(keys[cited], df[text_col][cited])
    }


def _text_units_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """The text-units frame under any of its names: its ids are what the
    answer's Sources markers cite and its document_id column is what maps
    them to documents — taken from the frames that ANSWERED, never re-read
    (spec 7.4)."""
    return next((df for name, df in frames.items() if frame_key(name) == "sources"), None)


@dataclass(frozen=True)
class Prepared:
    root: Path
    project_id: uuid.UUID
    config: Any
    frames: dict[str, pd.DataFrame]
    frames_ms: float


async def prepare_query(project: Project, method: str, *, config: Any = None) -> Prepared:
    """Config load (or reuse a caller-supplied one) -> frames -> frames_ms.

    No limiter and no user on purpose: this is the part the batch service
    shares with the interactive paths, and the batch is bounded by
    MAX_CONCURRENT_JOBS instead (spec 7.3). The `config` parameter exists
    so services/test_run_worker.py can load configuration once at worker start
    and reuse it for every question.
    """
    root = ws_path(project.id)
    if config is None:
        try:
            # settings.yaml + .env + pydantic validation: file I/O and CPU,
            # so off the loop (R1-73); the adapter memoises per file version
            config = await asyncio.to_thread(load_config, root)
        except Exception as exc:
            logger.exception("query config load failed (project %s)", project.id)
            raise QueryError("config", str(exc)[-500:]) from exc

    frames_start = time.perf_counter()
    cache = get_frame_cache()
    try:
        tables = tables_for(method)
        # A cold cache reads each parquet in its own thread; await them together
        loaded = await asyncio.gather(*(cache.get(root, table) for table in tables))
        frames = dict(zip(tables, loaded, strict=True))
    except WorkspaceNotIndexedError:
        raise
    except Exception as exc:  # e.g. corrupt parquet — 502, tail kept server-side
        logger.exception("frame load failed (project %s, method %s)", project.id, method)
        raise QueryError("search", str(exc)[-500:]) from exc
    frames_ms = (time.perf_counter() - frames_start) * 1000
    return Prepared(
        root=root, project_id=project.id, config=config, frames=frames, frames_ms=frames_ms
    )


async def execute_query(
    prepared: Prepared,
    method: str,
    query: str,
    response_type: str | None,
    *,
    g0: Generation,
    memo: CitationMemo | None = None,
) -> dict:
    """search -> citations -> timings. Non-streaming only: the adapter's
    search returns context_data, which is what the citation ENTRY TEXT
    joins against (see _citations for the document map). Streaming has no
    context_data and keeps its own tail in stream_query. `g0` is the
    generation the caller read BEFORE the frame load (spec 7.4 step 1) —
    the guard must bracket the documents read that enrichment performs
    after the search, so it cannot be read here."""
    exec_start = time.perf_counter()
    search_start = time.perf_counter()
    try:
        answer, context = await GraphragSearchAdapter().search(
            method,
            prepared.config,
            prepared.frames,
            query,
            response_type or DEFAULT_RESPONSE_TYPE,
        )
    except Exception as exc:
        logger.exception("search failed (project %s, method %s)", prepared.root.name, method)
        raise QueryError("search", str(exc)[-500:]) from exc
    search_ms = (time.perf_counter() - search_start) * 1000

    citations, citations_ms = await _citations(answer, context, prepared, g0, memo)

    return {
        "answer": answer,
        "context": [{"name": name, "rows": len(df)} for name, df in context.items()],
        "citations": citations,
        "timings": {
            "frames_ms": prepared.frames_ms,
            "search_ms": search_ms,
            "citations_ms": citations_ms,
            "total_ms": (time.perf_counter() - exec_start) * 1000,
        },
    }


async def _preamble(project: Project, user: User, method: str) -> tuple[Generation, Prepared]:
    """The interactive preamble both routes share: rate limit first (a
    cheap in-memory check before any I/O), then G0 BEFORE the frame load
    (spec 7.4 step 1: the guard must bracket the documents read that
    enrichment performs after the search), then config and frames."""
    get_rate_limiter().check(str(user.id), str(project.id))
    g0 = await read_generation(project.id)
    return g0, await prepare_query(project, method)


async def _citations(
    answer: str,
    context: dict[str, pd.DataFrame],
    prepared: Prepared,
    g0: Generation,
    memo: CitationMemo | None,
) -> tuple[list[dict], float]:
    """Markers joined against `context` for their entry text, cited text
    units mapped to documents through the cached parquet frames the search
    was handed (graphrag's context text-units frame has no document_id
    column, R4-40), falling back to the context's for modes that load no
    text_units table (global). Returns the citations and their duration."""
    start = time.perf_counter()
    text_units = _text_units_frame(prepared.frames)
    if text_units is None:
        text_units = _text_units_frame(context)
    citations = await enrich_sources(
        build_citations(answer, _cited_texts(answer, context)),
        text_units,
        prepared.root,
        prepared.project_id,
        g0=g0,
        memo=CitationMemo() if memo is None else memo,
    )
    return citations, (time.perf_counter() - start) * 1000


async def run_query(
    project: Project,
    user: User,
    method: str,
    query: str,
    response_type: str | None = None,
) -> dict:
    """Run one four-mode query; returns the API response body (never raises HTTP)."""
    total_start = time.perf_counter()
    g0, prepared = await _preamble(project, user, method)
    body = await execute_query(prepared, method, query, response_type, g0=g0)
    # total_ms spans the whole interactive request (limiter + preamble +
    # search), so it is measured from run_query's own entry.
    body["timings"]["total_ms"] = (time.perf_counter() - total_start) * 1000
    return body


async def stream_query(
    project: Project,
    user: User,
    method: str,
    query: str,
    response_type: str | None = None,
):
    """Streaming variant of run_query: an async generator yielding
    ("chunk", str) events, then ("citations", list) and ("done", timings).

    Same pre-checks in the same order (rate → config → frames) — any failure
    raises BEFORE the first chunk so the route can answer with plain JSON.
    Streaming returns no context_data, so citations join against the very
    frames handed to the adapter. An adapter failure after chunks were
    delivered yields ("error", INTERRUPTED_DETAIL) and ends the stream (the cause is
    logged server-side); before the first chunk it raises QueryError.
    """
    total_start = time.perf_counter()
    g0, prepared = await _preamble(project, user, method)

    gen = GraphragSearchAdapter().stream(
        method,
        prepared.config,
        prepared.frames,
        query,
        response_type or DEFAULT_RESPONSE_TYPE,
    )
    search_start = time.perf_counter()
    answer_parts: list[str] = []
    try:
        async for text in gen:
            answer_parts.append(text)
            yield ("chunk", text)
    except Exception as exc:
        logger.exception(
            "query stream failed (project %s, method %s, chunks=%d)",
            project.id,
            method,
            len(answer_parts),
        )
        if answer_parts:
            yield ("error", INTERRUPTED_DETAIL)
            return
        # Nothing delivered yet — surface as a pre-stream failure (JSON).
        raise QueryError("search", str(exc)[-500:]) from exc
    search_ms = (time.perf_counter() - search_start) * 1000

    # Streaming has no context_data: join markers against the cached frames
    citations, citations_ms = await _citations(
        "".join(answer_parts), prepared.frames, prepared, g0, None
    )

    yield ("citations", citations)
    yield (
        "done",
        {
            "frames_ms": prepared.frames_ms,
            "search_ms": search_ms,
            "citations_ms": citations_ms,
            "total_ms": (time.perf_counter() - total_start) * 1000,
        },
    )
