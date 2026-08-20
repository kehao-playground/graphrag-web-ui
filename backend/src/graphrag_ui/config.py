from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://graphrag:graphrag@localhost:5432/graphrag"
    workspaces_dir: str = "./data/workspaces"
    jwt_secret: str = "dev-secret-change-me"
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    upload_max_file_mb: int = 50
    project_quota_mb: int = 5000

@lru_cache
def get_settings() -> Settings:
    return Settings()
