"""Project .env endpoints: per-key management with masked reads (spec §6.1).

Permissions: writes are project:edit_settings (API keys are settings-
grade), listing is project:view. Audit actions: env.key_set / env.key_deleted with
payload {key} only. Values are secrets — no response body, error payloads
included, may ever contain a plaintext value. The app-level 422 handler
(api/errors.py) strips each error's `input`, so PATCH can use a plain
pydantic body; env_file's own messages never contain the value either.
"""

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, field_validator

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    ProjectEditSettings,
    ProjectView,
    get_current_user,
)
from graphrag_ui.services import env_file as env_service


class EnvKeyOut(BaseModel):
    key: str
    masked: str
    # True while the value is empty or graphrag init's `<API_KEY>` stand-in:
    # the key still has to be set before an index can call the model.
    is_placeholder: bool


class EnvOut(BaseModel):
    keys: list[EnvKeyOut]


# Cap on a PATCHed value: .env holds API keys and connection strings, and a
# value is also bounded below by the single-line rule — 64 KiB is far beyond
# any legitimate secret.
_MAX_VALUE_BYTES = 64 * 1024


class EnvKeyIn(BaseModel):
    key: str
    value: str

    @field_validator("value")
    @classmethod
    def _bounded(cls, v: str) -> str:
        # The bound is on BYTES: pydantic's max_length counts characters.
        if len(v.encode()) > _MAX_VALUE_BYTES:
            raise ValueError("value too large")
        return v


def register_env_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/{pid}/env", response_model=EnvOut)
    async def get_env(project: ProjectView):
        return EnvOut(keys=[EnvKeyOut(**e) for e in env_service.list_env(project)])

    @router.patch("/{pid}/env", status_code=status.HTTP_204_NO_CONTENT)
    async def set_env_key(
        project: ProjectEditSettings, body: EnvKeyIn, db: DbSession, user: CurrentUser
    ):
        await env_service.set_env_key(db, project, body.key, body.value, actor_id=user.id)

    @router.delete("/{pid}/env/{key}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_env_key(
        project: ProjectEditSettings, key: str, db: DbSession, user: CurrentUser
    ):
        # 404 env_key_not_found, 400 env_key_referenced (settings.yaml still
        # needs the key) and 409 project_indexing come from the service.
        await env_service.delete_env_key(db, project, key, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
