"""Subprocess execution of graphrag index/update (spec §6.3). All graphrag
subprocess touchpoints live in adapters (AGENTS.md). stdout+stderr stream to
the job's log file; cancellation is SIGTERM -> 30s grace -> SIGKILL; stats
are scanned from disk by job type (paths empirically verified 2026-08-21,
spec §13 實測表)."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

_ERROR_TAIL_CHARS = 4000
_CANCEL_GRACE_S = 30.0
_IO_POLL_S = 0.5


@dataclass
class RunResult:
    status: str  # succeeded | failed | cancelled
    exit_code: int | None
    error: str | None
    stats: dict | None


def log_path_for(root: Path, job_id: uuid.UUID) -> Path:
    p = root / "logs" / "jobs" / f"{job_id}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_stats(job_type: str, root: Path) -> dict | None:
    """stats.json by job type. update: newest update_output/*/delta/stats.json
    (timestamp dirs sort lexically). NEVER output/stats.json for update jobs —
    merge does not rewrite it (verified)."""
    try:
        if job_type == "index":
            f = root / "output" / "stats.json"
            return json.loads(f.read_text()) if f.exists() else None
        dirs = sorted((root / "update_output").glob("*/delta/stats.json"))
        return json.loads(dirs[-1].read_text()) if dirs else None
    except (OSError, ValueError):
        return None  # stats are best-effort; never fail the job on them


class IndexRunner:
    def __init__(self, argv_prefix: Sequence[str] = ("graphrag",)) -> None:
        self._prefix = tuple(argv_prefix)

    async def run(
        self,
        *,
        argv: list[str],
        root: Path,
        log_path: Path,
        job_type: str,
        heartbeat: Callable[[], Awaitable[None]],
        cancel_requested: Callable[[], bool],
    ) -> RunResult:
        proc = await asyncio.create_subprocess_exec(
            *self._prefix,
            *argv,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        cancelled = False

        async def _pump() -> None:
            # stream stdout(+stderr) to the log file as it arrives
            with log_path.open("ab") as fh:
                while True:
                    chunk = await proc.stdout.read(8192)
                    if not chunk:
                        break
                    fh.write(chunk)
                    fh.flush()

        async def _cancel_poll() -> None:
            nonlocal cancelled
            while proc.returncode is None:
                await asyncio.sleep(_IO_POLL_S)
                if cancel_requested():
                    cancelled = True
                    proc.terminate()
                    await asyncio.sleep(_CANCEL_GRACE_S)
                    if proc.returncode is None:
                        proc.kill()
                    return

        pump = asyncio.create_task(_pump())
        poller = asyncio.create_task(_cancel_poll())
        try:
            exit_code = await proc.wait()
        finally:
            await pump  # drain EOF
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
        if cancelled and exit_code != 0:
            status, error = "cancelled", None
        elif exit_code == 0:
            status, error = "succeeded", None
        else:
            status = "failed"
            tail = log_path.read_text(errors="replace")[-_ERROR_TAIL_CHARS:]
            from graphrag_ui.domain.jobs import error_annotation

            note = error_annotation(exit_code)
            error = (f"{note}\n{tail}" if note else tail).strip() or "no output"
        return RunResult(
            status=status, exit_code=exit_code, error=error, stats=read_stats(job_type, root)
        )
