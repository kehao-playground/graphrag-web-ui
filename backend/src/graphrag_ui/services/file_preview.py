"""Document preview: one bounded window of an input file, optionally
centered on a passage, and the historic citation locator that resolves a
stored passage (spec 7.4)."""

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import Project, TestResult, TestRun
from graphrag_ui.services.errors import CodedServiceError
from graphrag_ui.services.files import input_file
from graphrag_ui.services.input_scan import CHUNK_BYTES


class LocatorMismatchError(CodedServiceError):
    """A {result_id, entry_id} locator failed one of its three bindings
    (spec 7.4). Carries no detail on purpose: unknown and mismatched must
    stay indistinguishable, so every failure maps to one fixed 404 — never
    a 403, which would confirm the row exists."""

    code = "citation_not_found"


# Preview contract limits (spec 7.4): module-level constants, not env vars —
# the frontend sizes its drawer around the window and the API around the
# passage bound.
PREVIEW_WINDOW_BYTES = 64 * 1024
PASSAGE_MAX_BYTES = 4096


def _preview_core(path: Path, needle: bytes | None) -> dict:
    """One bounded window of the file, centered on the first occurrence of
    `needle` anywhere in it when given, else the head.

    The window cap bounds the RESPONSE, never the scan: with a passage the
    file is streamed in CHUNK_BYTES blocks carrying an overlap of
    len(needle) - 1 bytes, so a passage spanning a chunk boundary is still
    found (spec 7.4).
    """
    total = path.stat().st_size
    with path.open("rb") as fh:
        if needle is None:
            return {
                "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
                "offset": 0,
                "total_size": total,
                "match": False,
            }
        overlap = len(needle) - 1
        base = 0  # absolute offset of buf[0]
        carry = b""
        found = -1
        while True:
            chunk = fh.read(CHUNK_BYTES)
            if not chunk:
                break
            buf = carry + chunk
            idx = buf.find(needle)
            if idx != -1:
                found = base + idx
                break
            carry = buf[-overlap:] if overlap > 0 else b""
            base += len(buf) - len(carry)
        if found == -1:
            # Unmatched: the head plus match=False, rather than pretending.
            fh.seek(0)
            return {
                "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
                "offset": 0,
                "total_size": total,
                "match": False,
            }
        start = max(0, found - PREVIEW_WINDOW_BYTES // 2)
        fh.seek(start)
        return {
            "text": fh.read(PREVIEW_WINDOW_BYTES).decode("utf-8", errors="replace"),
            "offset": start,
            "total_size": total,
            "match": True,
        }


async def preview_file(project: Project, name: str, *, around: str | None = None) -> dict:
    """{"text", "offset", "total_size", "match"} for input/<name>; the
    bounded scan runs off the event loop (spec A4). A `removed` row has no
    file behind it, so there is nothing to preview (404)."""
    _, target = input_file(project, name)
    needle = around.encode("utf-8") if around is not None else None
    return await asyncio.to_thread(_preview_core, target, needle)


async def resolve_stored_passage(
    session: AsyncSession,
    project_id: uuid.UUID,
    result_id: uuid.UUID,
    entry_id: int,
    name: str,
) -> str:
    """The stored passage a historic locator points at (spec 7.4).

    Three bindings, any mismatch -> LocatorMismatchError: the result's run must
    belong to `project_id`, `entry_id` must name an entry of a Sources
    citation on that result, and that entry's stored source_name must equal
    `name` (the confused-deputy stop: without it, a real result_id paired
    with any filename would search a different document). One exception for
    all three so the caller cannot learn which binding failed.
    """
    citations = (
        await session.execute(
            select(TestResult.citations)
            .join(TestRun, TestRun.id == TestResult.run_id)
            .where(TestResult.id == result_id, TestRun.project_id == project_id)
        )
    ).scalar_one_or_none()
    if citations is not None:
        for citation in citations:
            if citation.get("label") != "Sources":
                continue
            for entry in citation.get("entries") or []:
                if entry.get("id") == entry_id:
                    # A matched entry without a usable stored passage (no
                    # source_name, no text) is as unopenable as no match.
                    if entry.get("source_name") == name and entry.get("text"):
                        return str(entry["text"])
                    raise LocatorMismatchError
    raise LocatorMismatchError
