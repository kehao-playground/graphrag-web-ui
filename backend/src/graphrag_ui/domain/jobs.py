"""Pure job rules: CLI argv mapping, exit-code annotation, status display.
No I/O, no graphrag imports (AGENTS.md layering)."""

from pathlib import Path

JOB_TYPES = ("index", "update", "test_run")
CLI_JOB_TYPES = ("index", "update")
# The CLI job types are the ones that read input/ and so freeze it (spec 5.2b).
FREEZING_JOB_TYPES = CLI_JOB_TYPES
JOB_METHODS = ("standard", "fast")
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = {"succeeded", "failed", "failed(interrupted)", "cancelled"}

# graphrag 3.1.2's built-in pipelines (graphrag/index/workflows/factory.py),
# keyed by (job type, method): the denominator of index/update progress
# (R3-36). A test diffs them against the pinned graphrag, so a bump that
# changes a pipeline fails the suite instead of skewing the count.
_STANDARD = (
    "create_base_text_units",
    "create_final_documents",
    "extract_graph",
    "finalize_graph",
    "extract_covariates",
    "create_communities",
    "create_final_text_units",
    "create_community_reports",
    "generate_text_embeddings",
)
_FAST = (
    "create_base_text_units",
    "create_final_documents",
    "extract_graph_nlp",
    "prune_graph",
    "finalize_graph",
    "create_communities",
    "create_final_text_units",
    "create_community_reports_text",
    "generate_text_embeddings",
)
_UPDATE = (
    "update_final_documents",
    "update_entities_relationships",
    "update_text_units",
    "update_covariates",
    "update_communities",
    "update_community_reports",
    "update_text_embeddings",
    "update_clean_state",
)
PIPELINE_WORKFLOWS: dict[tuple[str, str], tuple[str, ...]] = {
    ("index", "standard"): ("load_input_documents", *_STANDARD),
    ("index", "fast"): ("load_input_documents", *_FAST),
    ("update", "standard"): ("load_update_documents", *_STANDARD, *_UPDATE),
    ("update", "fast"): ("load_update_documents", *_FAST, *_UPDATE),
}


def build_argv(job_type: str, method: str, root: Path) -> list[str]:
    """graphrag CLI argv (without the executable). `update` must receive
    standard|fast — the CLI appends '-update' internally; passing
    'standard-update' would build 'standard-update-update' (source-verified).

    test_run is a valid job type with NO argv: it runs in-process through
    services/test_run_worker.py. Asking for one is a caller bug, not a silent
    empty list (spec 7.2)."""
    if job_type not in CLI_JOB_TYPES:
        msg = f"job type has no CLI argv: {job_type}"
        raise ValueError(msg)
    if method not in JOB_METHODS:
        msg = f"unknown method: {method}"
        raise ValueError(msg)
    return [job_type, "--root", str(root), "--method", method]


def error_annotation(exit_code: int) -> str | None:
    # exit 137 = 128+SIGKILL; asyncio proc.wait() reports signal deaths as
    # negative POSIX signal codes, so -9 is the same kernel OOM kill (spec §5)
    return "likely out of memory (OOM)" if exit_code in (137, -9) else None


def workflow_total(job_type: str, method: str, configured: object) -> int:
    """How many workflows the run will execute. A non-empty `workflows:`
    list in settings.yaml replaces the built-in pipeline outright (graphrag's
    PipelineFactory.create_pipeline); anything else falls back to it."""
    if isinstance(configured, list) and configured:
        return len(configured)
    return len(PIPELINE_WORKFLOWS[(job_type, method)])


def workflow_progress(stats: object, total: int) -> dict[str, int]:
    """jobs.progress for an index/update run: graphrag adds each workflow to
    stats.json's `workflows` map as it finishes (spec §6.3)."""
    workflows = stats.get("workflows") if isinstance(stats, dict) else None
    done = len(workflows) if isinstance(workflows, dict) else 0
    return {"done": min(done, total), "total": total}


def display_status(status: str, cancel_requested: bool) -> str:
    return "cancelling" if status == "running" and cancel_requested else status
