"""Pure job rules: CLI argv mapping, exit-code annotation, status display.
No I/O, no graphrag imports (AGENTS.md layering)."""

from pathlib import Path

JOB_TYPES = ("index", "update")
JOB_METHODS = ("standard", "fast")
TERMINAL_STATUSES = {"succeeded", "failed", "failed(interrupted)", "cancelled"}


def build_argv(job_type: str, method: str, root: Path) -> list[str]:
    """graphrag CLI argv (without the executable). `update` must receive
    standard|fast — the CLI appends '-update' internally; passing
    'standard-update' would build 'standard-update-update' (source-verified)."""
    if job_type not in JOB_TYPES:
        msg = f"unknown job type: {job_type}"
        raise ValueError(msg)
    if method not in JOB_METHODS:
        msg = f"unknown method: {method}"
        raise ValueError(msg)
    return [job_type, "--root", str(root), "--method", method]


def error_annotation(exit_code: int) -> str | None:
    # exit 137 = 128+SIGKILL; asyncio proc.wait() reports signal deaths as
    # negative POSIX signal codes, so -9 is the same kernel OOM kill (spec §5)
    return "likely out of memory (OOM)" if exit_code in (137, -9) else None


def display_status(status: str, cancel_requested: bool) -> str:
    return "cancelling" if status == "running" and cancel_requested else status
