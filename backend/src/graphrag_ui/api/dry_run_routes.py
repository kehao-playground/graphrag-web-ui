"""Dry-run validation endpoint (spec §6.1/§6.2): runs `graphrag index
--dry-run` synchronously — never queued. A validation failure is DATA
(ok=false + CLI output tail), not an HTTP error; only infrastructure
failures (CLI missing) become 5xx. No audit rows.
"""

import asyncio

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from graphrag_ui.adapters.workspace import WorkspaceInitError, dry_run
from graphrag_ui.api.deps import ProjectEditSettings, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.services.projects import ws_path
from graphrag_ui.services.settings import SettingsValidationError, check_workspace_settings


class DryRunOut(BaseModel):
    ok: bool
    output: str


def register_dry_run_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/dry-run", response_model=DryRunOut)
    async def run_dry_run(project: ProjectEditSettings):
        # dry-run validates settings drafts (spec §4.3) — settings-grade
        try:
            # R2-03: an escaping settings.yaml is a validation failure like
            # any other — reported as data, and the CLI is never forked on it
            # (so this route keeps its own except instead of the 400 table).
            await asyncio.to_thread(check_workspace_settings, project)
        except SettingsValidationError as e:
            return DryRunOut(ok=False, output=str(e))
        try:
            # module-level import above: tests monkeypatch dry_run_routes.dry_run
            result = await dry_run(ws_path(project.id))
        except WorkspaceInitError:
            raise ApiError(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "dry_run_failed", "graphrag dry-run failed"
            ) from None
        return DryRunOut(**result)

    app.include_router(router)
