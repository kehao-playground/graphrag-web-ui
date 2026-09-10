"""Citation -> source resolution, bracketed by a generation guard (spec 7.4).

A citation id is only meaningful against the artifacts that produced it:
human_readable_id is assigned per build, so Sources (7) from last week may
name a different text unit today. Resolving a historic citation against
current artifacts would not 404 - it would open THE WRONG DOCUMENT,
silently. So source_name is resolved while the answer is produced, for
every answer, and no endpoint resolves a citation id after the fact.

The guard does not stop the race; it detects it and refuses to guess, which
is the only honest option for a path that cannot hold the job mutex. The
answer text is always returned - only the links are withheld.
"""

import inspect
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from graphrag_ui.adapters.artifacts import resolve_document_titles
from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.adapters.models import Job, Project
from graphrag_ui.domain.artifacts import recover_filename
from graphrag_ui.services.index_snapshots import baseline_row, entries_of
from graphrag_ui.services.project_lock import FREEZING_JOB_TYPES

logger = logging.getLogger(__name__)

# Marker labels that cite text units (domain.citations folds both
# spellings onto the sources frame); every other label summarizes many
# documents and stays unlinked by product decision (spec 7.4).
_SOURCE_LABELS = frozenset({"sources", "source"})


@dataclass(frozen=True)
class Generation:
    baseline_snapshot_id: uuid.UUID | None
    artifact_epoch: int
    active_index_job: bool


async def read_generation(project_id: uuid.UUID) -> Generation:
    """Fresh read of the three facts, in its OWN session: an identity-map
    reuse would return G0's values at G1 and defeat the whole bracket."""
    async with get_session_factory()() as s:
        row = (
            await s.execute(
                select(Project.baseline_snapshot_id, Project.artifact_epoch).where(
                    Project.id == project_id
                )
            )
        ).one()
        active = (
            await s.execute(
                select(Job.id)
                .where(
                    Job.project_id == project_id,
                    Job.status.in_(("queued", "running")),
                    Job.type.in_(FREEZING_JOB_TYPES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return Generation(row[0], int(row[1]), active is not None)


def _trustworthy(g0: Generation, g1: Generation, baseline_epoch: int | None) -> bool:
    """All four conditions of spec 7.4 step 6.

    The FOURTH is the decisive one. The G0/G1 comparison detects an attempt
    inside the guarded interval; it says nothing about whether the artifacts
    already on disk at G0 came from the baseline. A failed job rewrites
    output/ in place and promotes nothing, so its damage outlives the
    interval and every later query would otherwise pass.
    """
    if baseline_epoch is None or g0.baseline_snapshot_id is None:
        return False
    return (
        g0.baseline_snapshot_id == g1.baseline_snapshot_id
        and g0.artifact_epoch == g1.artifact_epoch
        and not g0.active_index_job
        and not g1.active_index_job
        and g0.artifact_epoch == baseline_epoch
    )


def _is_sources(citation: dict) -> bool:
    return str(citation.get("label", "")).strip().lower() in _SOURCE_LABELS


def _unlink(citations: list[dict]) -> list[dict]:
    """Every entry of every citation renders unlinked (source_name None)."""
    for citation in citations:
        for entry in citation.get("entries", []):
            entry["source_name"] = None
    return citations


def _hrid_to_document(text_units: pd.DataFrame | None) -> dict[int, str]:
    """hrid -> document_id through the ALREADY-LOADED text_units frame,
    keying ids exactly like query._frame_texts: on human_readable_id when
    the column exists (cached parquet shape), else on int(id) (search
    context shape); non-int ids resolve nothing rather than raising."""
    if text_units is None or "document_id" not in text_units.columns:
        return {}
    id_col = "human_readable_id" if "human_readable_id" in text_units.columns else "id"
    out: dict[int, str] = {}
    for raw_id, document_id in zip(text_units[id_col], text_units["document_id"]):
        try:
            hrid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if document_id is None or (isinstance(document_id, float) and pd.isna(document_id)):
            continue
        out[hrid] = str(document_id)
    return out


async def _resolve(root: Path, document_ids: set[str]) -> dict[str, str]:
    """The batched resolver, one call for the whole id set. Accepts an
    awaitable wrapper too: the guard's barrier tests park on this exact
    module attribute between step 3 and step 4 of the sequence."""
    result: Any = resolve_document_titles(root, document_ids)
    if inspect.isawaitable(result):
        result = await result
    return result


async def enrich_sources(
    citations: list[dict],
    text_units: pd.DataFrame | None,
    root: Path,
    project_id: uuid.UUID,
    *,
    g0: Generation,
    memo: dict[str, str | None],
) -> list[dict]:
    """Steps 3-6 of the normative sequence (spec 7.4): map the cited
    text-unit ids to document ids through the loaded frame, read
    documents.parquet for the ids not already in `memo`, apply the
    BASELINE's title recovery against its ENTRY names, read G1, and attach
    the names only if all four guard conditions hold. `g0` is read by the
    caller before the frame load, which is why it is a parameter.

    `memo` is a per-run dict the batch service threads through every
    question; it is what makes the call-count contract (at most one
    resolver call per completed question, on unmemoized ids only)
    testable. Best-effort: any failure is logged and every source_name
    stays None - the answer itself is never at risk.
    """
    try:
        return await _enrich(citations, text_units, root, project_id, g0, memo)
    except Exception:
        logger.exception("citation enrichment failed (project %s)", project_id)
        return _unlink(citations)


async def _enrich(
    citations: list[dict],
    text_units: pd.DataFrame | None,
    root: Path,
    project_id: uuid.UUID,
    g0: Generation,
    memo: dict[str, str | None],
) -> list[dict]:
    if g0.baseline_snapshot_id is None:
        # No baseline pointer at G0: there is no provenance to resolve
        # against, and the resolver must never fall back to today's
        # configuration or input/ (spec 7.4) - unlink without another read.
        return _unlink(citations)

    hrid_to_document = _hrid_to_document(text_units)
    cited_hrids = {
        entry["id"]
        for citation in citations
        if _is_sources(citation)
        for entry in citation.get("entries", [])
    }
    document_ids = {hrid_to_document[hrid] for hrid in cited_hrids if hrid in hrid_to_document}
    if not document_ids:
        # e.g. a global answer cites no text units: nothing to resolve, so
        # documents.parquet is not read at all (call-count contract).
        return _unlink(citations)

    unmemoized = document_ids - memo.keys()
    titles: dict[str, str] = {}
    if unmemoized:
        titles = await _resolve(root, unmemoized)

    factory = get_session_factory()
    async with factory() as s:
        baseline = await baseline_row(s, project_id)
        entries = {} if baseline is None else await entries_of(s, baseline.id)
    if baseline is None:
        return _unlink(citations)

    # Filenames come from the baseline, never today's settings.yaml: the
    # candidates are the baseline's ENTRY names - the primary record -
    # because attributable_titles is a RESULT of this rule, not an input
    # (feeding it back in would make resolution circular with the skipped
    # computation and inherit its narrowings).
    recovery_available = baseline.title_recovery == "available"
    candidates = frozenset(entries)
    for document_id in unmemoized:
        title = titles.get(document_id)
        memo[document_id] = (
            None if title is None or not recovery_available else recover_filename(title, candidates)
        )

    g1 = await read_generation(project_id)
    attach = _trustworthy(g0, g1, baseline.artifact_epoch)

    for citation in citations:
        sources = _is_sources(citation)
        for entry in citation.get("entries", []):
            name = None
            if attach and sources:
                resolved_id = hrid_to_document.get(entry["id"])
                name = None if resolved_id is None else memo.get(resolved_id)
            entry["source_name"] = name
    return citations
