"""Tail a job log file from a byte offset (SSE source, spec §6.1). Ends when
the job is terminal AND the file is fully drained."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path


async def tail_log(
    log_path: Path, offset: int, *, finished: Callable[[], Awaitable[bool]], poll_s: float = 1.0
) -> AsyncGenerator[tuple[int, bytes], None]:
    pos = max(0, offset)
    while True:
        if log_path.exists():
            size = log_path.stat().st_size
            if size > pos:
                with log_path.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                pos += len(chunk)
                yield pos, chunk
                continue
            if await finished():
                return
        else:
            if await finished():
                return  # log pruned or job never started: nothing to send
        await asyncio.sleep(poll_s)
