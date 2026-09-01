from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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
    query_rate_limit_per_hour: int = 30
    auth_mode: Literal["local", "proxy"] = "local"
    proxy_admin_emails: str = ""
    proxy_auth_secret: str = ""

    @property
    def proxy_admin_set(self) -> frozenset[str]:
        """Lowercased PROXY_ADMIN_EMAILS; matching is case-insensitive (spec §9)."""
        return frozenset(e.strip().lower() for e in self.proxy_admin_emails.split(",") if e.strip())

    # The shared secret is proxy mode's entire trust anchor (spec §4): unlike
    # a password it is never rate-limited and never rotated, so a weak one is
    # a startup error, not a warning. Fires on the first get_settings() call
    # — reached during create_app() — so a misconfigured container exits
    # before serving a single request with a guessable anchor.
    @model_validator(mode="after")
    def _proxy_mode_needs_strong_secret(self) -> "Settings":
        if self.auth_mode == "proxy" and len(self.proxy_auth_secret) < 32:
            raise ValueError("AUTH_MODE=proxy requires PROXY_AUTH_SECRET >= 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
