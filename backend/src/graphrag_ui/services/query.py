"""Query use case (spec §6.1/§6.4): rate limit → load config → load frames
from the per-project cache → search via the adapter → join citations from
the returned context frames → shape the response with timings.

Error contract (route maps, service never touches HTTP):
- QueryRateLimitedError / WorkspaceNotIndexedError re-raised as-is (429/409)
- ConfigLoadError → QueryError(code="config") (500)
- adapter or frame-load failure → QueryError with the exception tail kept
  SERVER-SIDE only (logger.exception); clients get a fixed message (502)
"""

import logging
import time

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.frame_cache import WorkspaceNotIndexedError, get_frame_cache, tables_for
from graphrag_ui.adapters.graphrag_search import GraphragSearchAdapter, load_config
from graphrag_ui.adapters.models import Project, User
from graphrag_ui.domain.citations import build_citations
from graphrag_ui.services.projects import _ws_path
from graphrag_ui.services.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

# graphrag's documented default response format (same as the CLI).
DEFAULT_RESPONSE_TYPE = "multiple paragraphs"

# First present column wins when flattening a context frame to {id: text}.
_TEXT_COLUMNS = ("text", "title", "description", "name")

# Frame-name synonyms that all mean graphrag text units: search context keys
# them "sources"/"units" per mode, the cached parquet table is "text_units".
_TEXT_UNIT_KEYS = ("text_units", "units", "sources")


class QueryError(RuntimeError):
    """Query pipeline failure. code: "config" (500) | "search" (502).
    detail is for the server log only — routes return fixed messages."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _flatten_frames(frames: dict[str, pd.DataFrame]) -> dict[str, dict[int, str | None]]:
    """Flatten context frames to {frame_key: {id: text}} for the pure parser."""
    texts: dict[str, dict[int, str | None]] = {}
    for name, df in frames.items():
        entries = _frame_texts(df)
        if name in _TEXT_UNIT_KEYS:
            # graphrag names the text-units frame "units" or "sources"
            # depending on the mode, and its "Sources" markers always
            # reference text units (Task 1 note): expose one flattened entry
            # under every synonym so citations resolve regardless of which
            # side named it — search context OR the cached parquet tables the
            # streaming path joins against.
            for key in _TEXT_UNIT_KEYS:
                texts.setdefault(key, entries)
        else:
            texts[name] = entries
    return texts


def _frame_texts(df: pd.DataFrame) -> dict[int, str | None]:
    if "id" not in df.columns:
        return {}
    text_col = next((c for c in _TEXT_COLUMNS if c in df.columns), None)
    if text_col is None:
        return {}
    entries: dict[int, str | None] = {}
    for raw_id, text in zip(df["id"], df[text_col]):
        try:
            entry_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        # NaN cells render as text: null (domain keeps None for missing text)
        entries[entry_id] = None if pd.isna(text) else str(text)
    return entries


async def run_query(
    session: AsyncSession, project: Project, user: User, method: str, query: str,
    response_type: str | None = None,
) -> dict:
    """Run one four-mode query; returns the API response body (never raises HTTP)."""
    total_start = time.perf_counter()
    # Rate limit first: cheap in-memory check before any I/O.
    get_rate_limiter().check(str(user.id), str(project.id))

    root = _ws_path(project.id)
    try:
        config = load_config(root)
    except Exception as exc:
        logger.exception("query config load failed (project %s)", project.id)
        raise QueryError("config", str(exc)[-500:]) from exc

    frames_start = time.perf_counter()
    cache = get_frame_cache()
    try:
        frames = {table: await cache.get(root, table) for table in tables_for(method)}
    except WorkspaceNotIndexedError:
        raise
    except Exception as exc:  # e.g. corrupt parquet — 502, tail kept server-side
        logger.exception("frame load failed (project %s, method %s)", project.id, method)
        raise QueryError("search", str(exc)[-500:]) from exc
    frames_ms = (time.perf_counter() - frames_start) * 1000
    search_start = time.perf_counter()
    try:
        answer, context = await GraphragSearchAdapter().search(
            method, config, frames, query,
            response_type or DEFAULT_RESPONSE_TYPE,
        )
    except Exception as exc:
        logger.exception("search failed (project %s, method %s)", project.id, method)
        raise QueryError("search", str(exc)[-500:]) from exc
    search_ms = (time.perf_counter() - search_start) * 1000

    citations_start = time.perf_counter()
    citations = build_citations(answer, _flatten_frames(context))
    citations_ms = (time.perf_counter() - citations_start) * 1000

    return {
        "answer": answer,
        "context": [{"name": name, "rows": len(df)} for name, df in context.items()],
        "citations": citations,
        "timings": {
            "frames_ms": frames_ms,
            "search_ms": search_ms,
            "citations_ms": citations_ms,
            "total_ms": (time.perf_counter() - total_start) * 1000,
        },
    }


async def stream_query(
    session: AsyncSession, project: Project, user: User, method: str, query: str,
    response_type: str | None = None,
):
    """Streaming variant of run_query: an async generator yielding
    ("chunk", str) events, then ("citations", list) and ("done", timings).

    Same pre-checks in the same order (rate → config → frames) — any failure
    raises BEFORE the first chunk so the route can answer with plain JSON.
    Streaming returns no context_data, so citations join against the very
    frames handed to the adapter. An adapter failure after chunks were
    delivered yields ("error", "查詢中斷") and ends the stream (the cause is
    logged server-side); before the first chunk it raises QueryError.
    """
    total_start = time.perf_counter()
    get_rate_limiter().check(str(user.id), str(project.id))

    root = _ws_path(project.id)
    try:
        config = load_config(root)
    except Exception as exc:
        logger.exception("query config load failed (project %s)", project.id)
        raise QueryError("config", str(exc)[-500:]) from exc

    frames_start = time.perf_counter()
    cache = get_frame_cache()
    try:
        frames = {table: await cache.get(root, table) for table in tables_for(method)}
    except WorkspaceNotIndexedError:
        raise
    except Exception as exc:
        logger.exception("frame load failed (project %s, method %s)", project.id, method)
        raise QueryError("search", str(exc)[-500:]) from exc
    frames_ms = (time.perf_counter() - frames_start) * 1000

    gen = GraphragSearchAdapter().stream(
        method, config, frames, query, response_type or DEFAULT_RESPONSE_TYPE,
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
            project.id, method, len(answer_parts),
        )
        if answer_parts:
            yield ("error", "查詢中斷")
            return
        # Nothing delivered yet — surface as a pre-stream failure (JSON).
        raise QueryError("search", str(exc)[-500:]) from exc
    search_ms = (time.perf_counter() - search_start) * 1000

    citations_start = time.perf_counter()
    # Streaming has no context_data: join markers against the cached frames
    # we just streamed with.
    citations = build_citations("".join(answer_parts), _flatten_frames(frames))
    citations_ms = (time.perf_counter() - citations_start) * 1000

    yield ("citations", citations)
    yield ("done", {
        "frames_ms": frames_ms,
        "search_ms": search_ms,
        "citations_ms": citations_ms,
        "total_ms": (time.perf_counter() - total_start) * 1000,
    })
