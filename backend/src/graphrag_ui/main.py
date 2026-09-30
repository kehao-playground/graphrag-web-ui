import asyncio
import importlib.metadata
import logging
import re
import shutil
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from graphrag_ui.adapters.db import get_session_factory
from graphrag_ui.api.audit_routes import register_audit_routes
from graphrag_ui.api.auth_routes import register_auth_routes
from graphrag_ui.api.dry_run_routes import register_dry_run_routes
from graphrag_ui.api.env_routes import register_env_routes
from graphrag_ui.api.errors import (
    ApiError,
    api_error_handler,
    coded_error_handler,
    validation_error_handler,
)
from graphrag_ui.api.explore_routes import register_explore_routes
from graphrag_ui.api.files_routes import register_files_routes
from graphrag_ui.api.health_project_routes import register_health_project_routes
from graphrag_ui.api.health_routes import register_health_routes
from graphrag_ui.api.jobs_routes import register_jobs_routes
from graphrag_ui.api.middleware import UploadSizeGuard
from graphrag_ui.api.openapi import install_error_schema
from graphrag_ui.api.projects_routes import register_projects_routes
from graphrag_ui.api.query_routes import register_query_routes
from graphrag_ui.api.questions_routes import register_questions_routes
from graphrag_ui.api.roles_routes import register_roles_routes
from graphrag_ui.api.settings_routes import register_settings_routes
from graphrag_ui.api.test_runs_routes import register_test_runs_routes
from graphrag_ui.api.users_routes import register_users_routes
from graphrag_ui.services.auth import bootstrap_admin
from graphrag_ui.services.errors import CodedServiceError


def _graphrag_version() -> str:
    # Detected once at startup, then cached (spec §6.1).
    # The graphrag 3.x CLI has no --version option (typer leaves it
    # undeclared, exit 2), so the version is read from package metadata;
    # adapters still probe PATH for CLI availability since they invoke
    # `graphrag` via subprocess — guaranteed inside containers by the
    # Dockerfile's ENV PATH.

    if shutil.which("graphrag") is None:
        return "not-installed"
    try:
        return importlib.metadata.version("graphrag")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


_LOG_HANDLER_NAME = "graphrag_ui"
# Third-party loggers whose INFO lines are per-request noise in the api log
# (httpx logs every LLM call the in-process query path makes). graphrag's
# own index output goes to the per-job log file, not through here.
_QUIET_LOGGERS = ("httpx", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "graphrag")


_TOKEN_PARAM = re.compile(r"((?:^|[?&])token=)[^&\s]*")
_PROBE_PATHS = frozenset({"/api/health", "/api/ready"})


class _AccessLogFilter(logging.Filter):
    """Two edits to uvicorn's access lines, whose args are (client, method,
    full path, http version, status):

    - The SSE routes take the access token as `?token=` (EventSource cannot
      set headers), so the token would land in `docker logs api` (R2-25,
      decision D5): its value is redacted.
    - A successful liveness/readiness probe is dropped (F24-03): the compose
      healthcheck and the kubelet hit them every few seconds. A failing
      probe stays — that is the line an operator is looking for."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) == 5
            and isinstance(args[2], str)
            and args[2].split("?", 1)[0] in _PROBE_PATHS
            and isinstance(args[4], int)
            and 200 <= args[4] < 300
        ):
            return False
        if isinstance(record.args, tuple) and any(
            isinstance(a, str) and "token=" in a for a in record.args
        ):
            record.args = tuple(
                _TOKEN_PARAM.sub(r"\1[redacted]", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def configure_logging() -> None:
    """Root logger at INFO with level and timestamp on every line (decision
    D7: no LOG_LEVEL knob). uvicorn configures only its own loggers, which
    do not propagate to the root, so nothing is printed twice. Idempotent:
    create_app() runs many times in tests."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(h.get_name() == _LOG_HANDLER_NAME for h in root.handlers):
        handler = logging.StreamHandler()
        handler.set_name(_LOG_HANDLER_NAME)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _AccessLogFilter) for f in access.filters):
        access.addFilter(_AccessLogFilter())


async def _retention_loop(stop: asyncio.Event) -> None:
    """Daily retention sweep (spec §6.3): once at startup, then every 24h.
    A failing sweep logs and waits for the next cycle; the stop event ends
    the loop promptly on shutdown."""
    while not stop.is_set():
        try:
            from graphrag_ui.services.retention import sweep_all

            await sweep_all()
        except Exception:
            logging.getLogger(__name__).warning("retention sweep failed", exc_info=True)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.graphrag_version = _graphrag_version()  # detected once, then cached (spec §6.1)
    async with get_session_factory()() as s:
        await bootstrap_admin(s)
    from graphrag_ui.services.runner_loop import run_loop

    app.state.runner_stop = asyncio.Event()
    app.state.runner_task = asyncio.create_task(run_loop(app.state.runner_stop))
    # Same stop event as the runner: setting it wakes both loops at shutdown.
    app.state.retention_task = asyncio.create_task(_retention_loop(app.state.runner_stop))
    yield
    app.state.runner_stop.set()
    # Cancel cleanly even if a sweep is mid-flight; the next daily pass
    # reclaims whatever this one skipped.
    app.state.retention_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.retention_task
    # In-flight subprocesses are NOT drained here (they keep writing to the
    # job log/DB); the next boot's stale reconcile finalizes them (spec §10).
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(app.state.runner_task, timeout=5)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="GraphRAG Web UI", lifespan=lifespan)
    # Starlette types every handler against bare Exception; a handler
    # narrowed to its own exception class cannot satisfy that signature.
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(CodedServiceError, coded_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    register_health_routes(app)
    # BEFORE projects_routes (contractual, same hazard as explore's
    # /artifacts/graph): /api/projects/health must not fall through to
    # /api/projects/{pid}, whose uuid parse of "health" is a 422.
    register_health_project_routes(app)
    register_auth_routes(app)
    register_users_routes(app)
    register_roles_routes(app)
    register_audit_routes(app)
    register_projects_routes(app)
    register_files_routes(app)
    app.add_middleware(UploadSizeGuard)
    register_env_routes(app)
    register_settings_routes(app)
    register_jobs_routes(app)
    register_dry_run_routes(app)
    register_query_routes(app)
    register_questions_routes(app)
    register_test_runs_routes(app)
    register_explore_routes(app)
    install_error_schema(app)
    return app
