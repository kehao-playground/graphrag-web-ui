"""Pure-domain citation parsing for GraphRAG answer markers.

Answers returned by ``graphrag.api`` embed inline markers like
``[Data: Sources (2)]`` or ``[Data: Entities (12, 34); Reports (5)]``.
This module parses those markers and joins their ids against a
pre-flattened ``{frame_key: {id: text}}`` mapping — the adapter/service
layer flattens DataFrames before calling in, keeping the domain layer
free of pandas and I/O.
"""

import re

__all__ = ["GROUP_RE", "MARKER_RE", "build_citations", "cited_ids", "frame_key", "parse_markers"]

# Matches the full bracketed marker, e.g. "[Data: Entities (12, 34); Reports (5)]".
MARKER_RE = re.compile(r"\[Data:\s*([^\]]+)\]")

# Matches one label group inside a marker, e.g. "Entities (12, 34)".
GROUP_RE = re.compile(r"([A-Za-z ]+?)\s*\(([\d,\s]+)\)")

# The ONE canonical frame key per normalized label: every spelling of text
# units folds onto "sources" and every spelling of community reports onto
# "reports" (R1-89), so the service maps each frame name once through
# frame_key() and a marker and its frame always meet on the same key.
_LABEL_KEYS = {
    "sources": "sources",
    "source": "sources",
    "text_units": "sources",
    "text_unit": "sources",
    "units": "sources",
    "unit": "sources",
    "entities": "entities",
    "entity": "entities",
    "relations": "relationships",
    "relation": "relationships",
    "relationships": "relationships",
    "relationship": "relationships",
    "reports": "reports",
    "report": "reports",
    "community_reports": "reports",
    "community_report": "reports",
    "communities": "communities",
    "community": "communities",
}


def parse_markers(text: str) -> list[tuple[str, list[int]]]:
    """Extract ordered, deduped ``(label, ids)`` pairs from inline markers.

    Malformed groups are skipped silently; repeated ``(label, ids)`` pairs
    keep only their first position.
    """
    if not text:
        return []
    results: list[tuple[str, list[int]]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for match in MARKER_RE.finditer(text):
        for part in match.group(1).split(";"):
            group = GROUP_RE.fullmatch(part.strip())
            if group is None:
                continue
            label = group.group(1).strip()
            ids = _unique_ids(group.group(2))
            if not ids:
                continue
            key = (frame_key(label), tuple(ids))
            if key in seen:
                continue
            seen.add(key)
            results.append((label, ids))
    return results


# str | None on the inner value, not str: entries are built with
# frame.get(i), and a marker citing an id absent from the frame is normal
# (the LLM cites report ids that were never indexed). The callers always
# passed such maps; only the annotation claimed otherwise.
def build_citations(text: str, texts_by_key: dict[str, dict[int, str | None]]) -> list[dict]:
    frames = texts_by_key or {}
    citations: list[dict] = []
    for label, ids in parse_markers(text):
        frame = frames.get(frame_key(label))
        entries = [] if frame is None else [{"id": i, "text": frame.get(i)} for i in ids]
        citations.append({"label": label, "ids": ids, "entries": entries})
    return citations


def cited_ids(text: str) -> dict[str, set[int]]:
    """Every id the answer's markers cite, grouped by canonical frame key —
    the only rows the caller needs to flatten (R1-72)."""
    wanted: dict[str, set[int]] = {}
    for label, ids in parse_markers(text):
        wanted.setdefault(frame_key(label), set()).update(ids)
    return wanted


def frame_key(label: str) -> str:
    """Normalize a marker label or a frame name to its canonical frame key
    (never raises)."""
    normalized = re.sub(r"\s+", "_", label.strip().lower())
    return _LABEL_KEYS.get(normalized, normalized)


def _unique_ids(raw: str) -> list[int]:
    """Parse comma/space separated ids, preserving order, dropping repeats."""
    ids: list[int] = []
    for token in re.findall(r"\d+", raw):
        value = int(token)
        if value not in ids:
            ids.append(value)
    return ids
