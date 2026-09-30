"""Shared service error bases.

CodedServiceError is every failure a client may see: api/errors.py maps
each subclass to an HTTP status once, for the whole app (R1-11/R1-30).
ServicePipelineError is deliberately NOT one of them — its detail is
server-log-only material (spec A7), so the query and explore routes map it
to fixed messages themselves.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

INTERRUPTED_DETAIL = "query interrupted"

# How much of an exception's text a pipeline error keeps for the server
# log: the tail, where the provider's actual complaint usually sits.
_DETAIL_TAIL = 500


class CodedServiceError(Exception):
    """A service failure with a stable wire code (i18n spec §4.1).

    `code` is a class attribute for single-meaning errors and a constructor
    argument for the validation families (files, settings, env) whose code
    names the rule that failed. The message is the legacy `detail` unless
    api/errors.py pins a fixed one for the class. Subclasses mix in the
    builtin base their callers historically caught (ValueError,
    LookupError, RuntimeError); every *NotFoundError is a LookupError.
    """

    code: str = "service_error"

    def __init__(
        self, detail: str = "", *, code: str | None = None, params: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        if code is not None:
            self.code = code
        self.params = params


class ServicePipelineError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@contextmanager
def pipeline_step(
    error_cls: type[ServicePipelineError],
    step: str,
    log_msg: str,
    *log_args: object,
    passthrough: tuple[type[BaseException], ...] = (),
) -> Iterator[None]:
    """One step of the query or explore pipeline (R1-76): an unexpected
    failure is logged with its traceback and re-raised as
    `error_cls(step, <tail of the message>)`; `passthrough` errors are
    re-raised as they are."""
    try:
        yield
    except passthrough:
        raise
    except Exception as exc:
        logging.getLogger(error_cls.__module__).exception(log_msg, *log_args)
        raise error_cls(step, str(exc)[-_DETAIL_TAIL:]) from exc


class NotIndexedError(CodedServiceError, RuntimeError):
    """The workspace has no index output to read yet (query, explore).
    The adapters' own not-indexed errors are translated to this one, so
    the api layer never imports them (R1-42). Maps to 409."""

    code = "not_indexed"


@contextmanager
def not_indexed_on(*adapter_errors: type[Exception]) -> Iterator[None]:
    """Translate an adapter's not-indexed error into NotIndexedError."""
    try:
        yield
    except adapter_errors as exc:
        raise NotIndexedError(str(exc)) from exc


class ProjectIndexingError(CodedServiceError, RuntimeError):
    """An index/update job holds the project; input and configuration are
    frozen for its duration (spec 5.2b). Maps to 409."""

    code = "project_indexing"

    def __init__(self, job_id: str, job_type: str) -> None:
        super().__init__(f"project is being indexed by job {job_id}", params={"job_type": job_type})


class JobConflictError(CodedServiceError, RuntimeError):
    """Another queued/running job for this project (DB mutex), or a project
    operation that cannot run while one is active. Maps to 409
    job_conflict."""

    code = "job_conflict"
