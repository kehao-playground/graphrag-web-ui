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


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str
