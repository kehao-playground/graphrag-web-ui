"""Batch execution of test_run jobs (spec 7.2).

Task 2 lands only the runner-loop dispatch seam; Task 5 implements the
real worker — walk the run's placeholder test_results, execute each
question through the shared query core, write back answers and timings."""

import uuid
from collections.abc import Callable
from pathlib import Path

from graphrag_ui.adapters.index_runner import RunResult


async def execute_test_run(
    job_id: uuid.UUID, root: Path, *, cancel_requested: Callable[[], bool]
) -> RunResult:
    """Execute one test_run job to a terminal RunResult.

    Stub until Task 5: raising (instead of returning a fake success) fails
    the job loudly if anything dispatches a test_run early."""
    raise NotImplementedError("test_run batch execution lands in Task 5")
