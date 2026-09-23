"""Shared base for service pipeline errors (spec A7).

code names the failing step; detail is server-log-only material — routes
return fixed zh-TW messages, never these strings.
"""

INTERRUPTED_DETAIL = "query interrupted"


class ServicePipelineError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class ProjectIndexingError(RuntimeError):
    """An index/update job holds the project; input and configuration are
    frozen for its duration (spec 5.2b). Routes map to 409."""

    def __init__(self, job_id: str, job_type: str) -> None:
        super().__init__(f"project is being indexed by job {job_id}")
        self.code = "project_indexing"
        self.params = {"job_type": job_type}


class JobConflictError(RuntimeError):
    """Another queued/running job for this project (DB mutex), or a project
    operation that cannot run while one is active. Routes map to 409
    job_conflict."""
