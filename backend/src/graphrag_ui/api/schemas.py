from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RoleOut(BaseModel):
    """One role catalog entry; user_count/member_count are populated only
    by GET /api/admin/roles (spec §7)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    user_count: int | None = None
    member_count: int | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        # pydantic 2 does not implicitly coerce UUID to str; Role.id is a UUID
        return str(v) if isinstance(v, UUID) else v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    roles: list[RoleOut] = []
    permissions: list[str] = []  # union of roles' atoms (spec §7)
    is_active: bool
    must_change_password: bool

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        # pydantic 2 does not implicitly coerce UUID to str; User.id is a UUID
        return str(v) if isinstance(v, UUID) else v


def user_out(user: object, roles: Sequence) -> UserOut:
    """Build UserOut from a User row plus its loaded global roles.
    Duck-typed on purpose: schemas stay free of ORM imports."""
    role_outs = [RoleOut.model_validate(r) for r in roles]
    perms: set[str] = set()
    for r in roles:
        perms.update(r.permissions or [])
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=role_outs,
        permissions=sorted(perms),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
    )


class UserBriefOut(BaseModel):
    """Narrow list shown to every logged-in user (for picking users in member
    management). Deliberately omits admin fields like roles / permissions /
    must_change_password."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    is_active: bool

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        return str(v) if isinstance(v, UUID) else v


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str


class AuthConfigOut(BaseModel):
    """Runtime auth mode for SPA boot detection (spec §5.3)."""

    auth_mode: Literal["local", "proxy"]


class JobCreateIn(BaseModel):
    type: Literal["index", "update"]
    method: Literal["standard", "fast"]


class JobOut(BaseModel):
    """API contract for a job row; frontend types.ts mirrors these keys
    (spec §6.1). argv included so the UI can show the exact CLI invocation."""

    id: str
    project_id: str
    type: str
    method: str
    status: str
    display_status: str
    cancel_requested_at: datetime | None
    exit_code: int | None
    error: str | None
    stats: dict | None
    queued_by: str
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    argv: list[str]


class LastRunOut(BaseModel):
    type: str
    status: str
    finished_at: datetime | None
    total_runtime_seconds: float | None
    num_documents: int | None
    update_documents: int | None


class PreflightOut(BaseModel):
    active_job: JobOut | None
    last_run: LastRunOut | None
    cache_bytes: int
    cache_quota_mb: int
    disk_free_mb: int
    disk_watermark_mb: int
