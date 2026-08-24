"""Dry-run validation endpoint (spec §6.1/§6.2): runs `graphrag index
--dry-run` synchronously — never queued. A validation failure is DATA
(ok=false + CLI output tail), not an HTTP error; only infrastructure
failures (CLI missing) become 5xx. No audit rows.
"""

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from graphrag_ui.adapters.workspace import WorkspaceInitError, dry_run
from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.projects import get_project_role, ws_path


class DryRunOut(BaseModel):
    ok: bool
    output: str


def register_dry_run_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.post("/{pid}/dry-run", response_model=DryRunOut)
    async def run_dry_run(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        try:
            # module-level import above: tests monkeypatch dry_run_routes.dry_run
            result = await dry_run(ws_path(project.id))
        except WorkspaceInitError:
            raise ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR,
                           "dry_run_failed", "graphrag dry-run failed") from None
        return DryRunOut(**result)

    app.include_router(router)
