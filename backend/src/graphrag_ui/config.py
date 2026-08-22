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
    max_concurrent_jobs: int = 2
    job_log_retention_days: int = 30
    job_log_failed_retention_days: int = 90
    update_output_keep_latest: int = 2
    cache_quota_mb: int = 2048
    disk_watermark_mb: int = 2048
    query_cache_mb: int = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
