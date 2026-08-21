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
    # exit 137 = 128+SIGKILL; under a container memory limit the kernel's
    # OOM killer is the usual sender (spec §5)
    return "疑似記憶體不足(OOM)" if exit_code == 137 else None


def display_status(status: str, cancel_requested: bool) -> str:
    return "cancelling" if status == "running" and cancel_requested else status
