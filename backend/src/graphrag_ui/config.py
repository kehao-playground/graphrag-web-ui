from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that have ever shipped in `.env.example` / helm values as a stand-in
# for a real JWT_SECRET. Matched whole (after strip/lower), never as a
# substring — a generated secret that happens to embed one is still fine.
_PLACEHOLDER_SECRETS = frozenset({"", "dev-secret-change-me", "change-me", "changeme"})
# Same idea for the bootstrap admin: the `.env.example` literal, matched
# whole. Empty is NOT in this set — it is the documented "create no admin"
# switch (services/auth.bootstrap_admin), not a weak password.
_PLACEHOLDER_PASSWORDS = frozenset({"bootstrap-admin-change-me", "change-me", "changeme"})
_BOOTSTRAP_PASSWORD_MIN_LEN = 12


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://graphrag:graphrag@localhost:5432/graphrag"
    workspaces_dir: str = "./data/workspaces"
    jwt_secret: str = ""
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
    graph_node_limit: int = 2000
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

    # Same argument, same enforcement, for local mode's trust anchor. Anyone
    # holding JWT_SECRET can sign an access token for any user id, so a
    # guessable one is a full authentication bypass — and the placeholders
    # below are the values a `cp .env.example .env` deployment inherits, i.e.
    # public knowledge. Local mode only: proxy mode registers no login or
    # refresh routes and issues no tokens at all.
    @model_validator(mode="after")
    def _local_mode_needs_strong_jwt_secret(self) -> "Settings":
        if self.auth_mode != "local":
            return self
        if self.jwt_secret.strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "JWT_SECRET is still the shipped placeholder — generate one with "
                "`openssl rand -hex 32`"
            )
        if len(self.jwt_secret) < 32:
            raise ValueError(
                "AUTH_MODE=local requires JWT_SECRET >= 32 characters — generate one "
                "with `openssl rand -hex 32`"
            )
        return self

    # The bootstrap admin is the first — for a while the only — account, and
    # whoever logs in with it first owns the deployment. Compose's `:?` guard
    # checks presence only, so a `cp .env.example .env` that replaced
    # JWT_SECRET alone still ships the placeholder; must_change_password is
    # the second line of defence, this is the first. Local mode only:
    # bootstrap_admin() is a no-op in proxy mode (spec §5.2).
    @model_validator(mode="after")
    def _local_mode_needs_real_bootstrap_password(self) -> "Settings":
        password = self.bootstrap_admin_password
        if self.auth_mode != "local" or not password:
            return self
        if password.strip().lower() in _PLACEHOLDER_PASSWORDS:
            raise ValueError(
                "BOOTSTRAP_ADMIN_PASSWORD is still the shipped placeholder — set a real "
                "password (or leave it empty to create no bootstrap admin)"
            )
        if len(password) < _BOOTSTRAP_PASSWORD_MIN_LEN:
            raise ValueError(
                f"BOOTSTRAP_ADMIN_PASSWORD must be >= {_BOOTSTRAP_PASSWORD_MIN_LEN} characters "
                "(or empty to create no bootstrap admin)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
