"""Project .env endpoints: per-key management with masked reads (task brief 4).

Permissions: writes are editor+ (Action.edit_content), listing is viewer+
(Action.view_project). Audit actions: env.key_set / env.key_deleted with
payload {key} only. Values are secrets — no response body, error payloads
included, may ever contain a plaintext value. That is why PATCH parses the
body manually instead of via a pydantic model: FastAPI's 422 echoes the
raw input, which would return the submitted secret to the client.
"""

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Action, can
from graphrag_ui.services.env_file import (
    EnvValidationError,
    delete_env_key,
    list_env,
    set_env_key,
)
from graphrag_ui.services.projects import get_project_role


class EnvKeyOut(BaseModel):
    key: str
    masked: str


class EnvOut(BaseModel):
    keys: list[EnvKeyOut]


# Cap on a PATCHed value: .env holds API keys and connection strings, and a
# value is also bounded below by the single-line rule — 64 KiB is far beyond
# any legitimate secret. Enforced inside the manual-parse path so the error
# stays a fixed message that never echoes the value.
_MAX_VALUE_BYTES = 64 * 1024


async def _secret_body(request: Request) -> dict:
    """{"key": str, "value": str} with fixed-message errors only."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "env_invalid_body",
                       "invalid body") from None
    if (not isinstance(body, dict) or not isinstance(body.get("key"), str)
            or not isinstance(body.get("value"), str)):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "env_key_value_required",
                       "key and value are required")
    if len(body["value"].encode()) > _MAX_VALUE_BYTES:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "env_value_too_large",
                       "value too large")
    return body


def register_env_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/{pid}/env", response_model=EnvOut)
    async def get_env(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.view_project,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        return EnvOut(keys=[EnvKeyOut(**e) for e in list_env(project)])

    @router.patch("/{pid}/env", status_code=status.HTTP_204_NO_CONTENT)
    async def patch_env(pid: uuid.UUID, request: Request,
                        db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        body = await _secret_body(request)
        try:
            await set_env_key(db, project, body["key"], body["value"],
                              actor_id=user.id)
        except EnvValidationError as e:
            # str(e) may echo the (non-secret) key but never the value —
            # env_file's messages are fixed to keep it that way
            raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, str(e),
                           e.params) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/{pid}/env/{key}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_env(pid: uuid.UUID, key: str,
                         db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(user.role, user.is_active, Action.edit_content,
                   await get_project_role(db, pid, user.id)):
            raise _forbidden()
        try:
            await delete_env_key(db, project, key, actor_id=user.id)
        except KeyError:
            raise ApiError(status.HTTP_404_NOT_FOUND, "env_key_not_found",
                           "key not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
