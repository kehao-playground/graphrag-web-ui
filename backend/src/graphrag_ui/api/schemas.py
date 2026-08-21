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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: object) -> object:
        # pydantic 2 不會把 UUID 隱性轉成 str;User.id 是 UUID
        return str(v) if isinstance(v, UUID) else v


class UserBriefOut(BaseModel):
    """給所有已登入使用者的窄清單(成員管理選人用)。
    刻意不含 role / must_change_password 等管理資訊。"""

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
