"""Subprocess execution of graphrag index/update (spec §6.3). All graphrag
subprocess touchpoints live in adapters (AGENTS.md). stdout+stderr stream to
the job's log file; cancellation is SIGTERM -> 30s grace -> SIGKILL; stats
are scanned from disk by job type (paths empirically verified 2026-08-21,
spec §13 verification table)."""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from graphrag_ui.adapters.workspace_env import subprocess_env

_ERROR_TAIL_CHARS = 4000
_CANCEL_GRACE_S = 30.0
_IO_POLL_S = 0.5

logger = logging.getLogger(__name__)


def _log_tail(log_path: Path) -> str:
    """The last _ERROR_TAIL_CHARS characters of the log, read from its end:
    a failed multi-hour job's log is never loaded whole. UTF-8 needs at most
    four bytes per character, so that many bytes always cover the tail."""
    with log_path.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 4 * _ERROR_TAIL_CHARS))
        return fh.read().decode(errors="replace")[-_ERROR_TAIL_CHARS:]


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
        # Allowlisted environment + the workspace .env (R2-01): the child
        # must not see JWT_SECRET, DATABASE_URL or any other API secret,
        # since settings.yaml `${VAR}` placeholders resolve from its environ.
        # stdout+stderr go straight to the log file: the child writes it
        # itself, so no per-chunk write runs on the event loop (R1-73).
        with log_path.open("ab") as log:
            proc = await asyncio.create_subprocess_exec(
                *self._prefix,
                *argv,
                cwd=root,
                env=subprocess_env(root),
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
            )
        # The log file is named by the job id (log_path_for).
        logger.info("job %s spawned: pid=%d argv=%s", log_path.stem, proc.pid, " ".join(argv))
        cancelled = False

        async def _cancel_poll() -> None:
            nonlocal cancelled
            while proc.returncode is None:
                await asyncio.sleep(_IO_POLL_S)
                if cancel_requested():
                    cancelled = True
                    # The child may exit between the loop check and here;
                    # asyncio then raises ProcessLookupError on the signal
                    # (R2-11), which must not turn the job into `failed`.
                    if proc.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            proc.terminate()
                    await asyncio.sleep(_CANCEL_GRACE_S)
                    if proc.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            proc.kill()
                    return

        poller = asyncio.create_task(_cancel_poll())
        try:
            exit_code = await proc.wait()
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
        if cancelled and exit_code != 0:
            status, error = "cancelled", None
        elif exit_code == 0:
            status, error = "succeeded", None
        else:
            status = "failed"
            tail = _log_tail(log_path)
            from graphrag_ui.domain.jobs import error_annotation

            note = error_annotation(exit_code)
            error = (f"{note}\n{tail}" if note else tail).strip() or "no output"
        return RunResult(
            status=status, exit_code=exit_code, error=error, stats=read_stats(job_type, root)
        )
