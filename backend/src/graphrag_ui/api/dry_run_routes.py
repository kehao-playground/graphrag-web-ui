"""Dry-run validation endpoint (spec §6.1/§6.2): runs `graphrag index
--dry-run` synchronously — never queued. A validation failure is DATA
(ok=false + CLI output tail), not an HTTP error; only infrastructure
failures (CLI missing) become 5xx. No audit rows.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from graphrag_ui.api.deps import ProjectEditSettings, get_current_user
from graphrag_ui.services.settings import dry_run_project


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
        return DryRunOut(**await dry_run_project(project))

    app.include_router(router)
