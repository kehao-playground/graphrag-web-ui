"""Audit log read endpoint (users:manage only).

Every write path already recorded rows — user.created, user.role_promoted,
file.uploaded, file.deleted, env.key_set, env.key_deleted and the rest — but
nothing could read them back, so the trail existed only for whoever had a
psql prompt. Read-only on purpose: audit rows are never edited or deleted
through the API, and the retention sweep is the only thing that removes
anything.

Gated on Atom.users_manage, the same right that governs the admin user and
role pages: the payloads name users and files, so it is not view-any
material.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from graphrag_ui.api.deps import DbSession, require_atom
from graphrag_ui.api.schemas import AuditPageOut
from graphrag_ui.domain.permissions import Atom
from graphrag_ui.services.audit import list_audit


def register_audit_routes(app):
    # Router built inside the function (same as the other route modules):
    # create_app() is called repeatedly in tests.
    router = APIRouter(
        prefix="/api/admin/audit", dependencies=[Depends(require_atom(Atom.users_manage))]
    )

    @router.get("", response_model=AuditPageOut)
    async def get_audit(
        db: DbSession,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        action: str | None = Query(default=None, max_length=50),
        target_type: str | None = Query(default=None, max_length=30),
        # Annotated form: B008 fires on a bare Query() default here (the
        # other params' calls carry constraints, this one would not).
        actor_id: Annotated[uuid.UUID | None, Query()] = None,
    ):
        rows, total = await list_audit(
            db,
            limit=limit,
            offset=offset,
            action=action,
            target_type=target_type,
            actor_id=actor_id,
        )
        return AuditPageOut(rows=rows, total=total)

    app.include_router(router)
