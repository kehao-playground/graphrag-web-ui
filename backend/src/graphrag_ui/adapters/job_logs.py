"""Tail a job log file from a byte offset (SSE source, spec §6.1). Ends when
the job is terminal AND the file is fully drained.

Reads are bounded (`chunk_bytes`) and run in a worker thread, so a client
(re)connecting at offset 0 to a long log never pulls the whole file onto
the event loop in one read (R1-79, R2-18). A chunk never ends inside a UTF-8
sequence: each one decodes on its own, and every yielded offset is a
character boundary a client can resume from. `finished()` costs a database
round trip, so it is asked only when the file has nothing new, and at most
once per `finished_every_s`."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

CHUNK_BYTES = 256 * 1024
FINISHED_EVERY_S = 5.0


def _read(log_path: Path, pos: int, size: int) -> bytes:
    try:
        with log_path.open("rb") as fh:
            fh.seek(pos)
            return fh.read(size)
    except FileNotFoundError:
        return b""


def _complete_utf8(chunk: bytes) -> int:
    """Length of the longest prefix that does not end inside a UTF-8
    sequence (at most the last three bytes are held back)."""
    for back in range(1, min(4, len(chunk)) + 1):
        byte = chunk[-back]
        if byte < 0x80:
            return len(chunk)  # ASCII: nothing pending
        if byte >= 0xC0:  # lead byte: complete only if its tail is all here
            need = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return len(chunk) if back >= need else len(chunk) - back
    return len(chunk)  # only continuation bytes: malformed, pass through


async def tail_log(
    log_path: Path,
    offset: int,
    *,
    finished: Callable[[], Awaitable[bool]],
    poll_s: float = 1.0,
    chunk_bytes: int = CHUNK_BYTES,
    finished_every_s: float = FINISHED_EVERY_S,
) -> AsyncGenerator[tuple[int, bytes], None]:
    loop = asyncio.get_running_loop()
    pos = max(0, offset)
    last_check = float("-inf")
    draining = False  # finished() said yes: read to EOF, then stop
    while True:
        chunk = await asyncio.to_thread(_read, log_path, pos, chunk_bytes)
        cut = _complete_utf8(chunk)
        if cut == 0 and chunk and (draining or len(chunk) >= 4):
            cut = len(chunk)  # a job that died mid-character still drains
        if cut:
            pos += cut
            yield pos, chunk[:cut]
            continue
        if draining:
            return
        if loop.time() - last_check >= finished_every_s:
            last_check = loop.time()
            if await finished():
                # Bytes can land between the last read and this answer.
                draining = True
                continue
        await asyncio.sleep(poll_s)
