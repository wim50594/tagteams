"""
Central settings – all values sourced from environment variables.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    jwt_secret: str = ""

    admin_bootstrap: bool = False
    admin_username: str = "admin"
    admin_password: str = ""

    # Primary relational database (PostgreSQL, SQLite, ...)
    database_url: str = "sqlite+aiosqlite:///./data/tagteams.db"

    # Redis is optional and used only as a cache. If unset, caching is
    # simply skipped and everything falls back to the RDBMS.
    redis_url: str | None = None
    cache_ttl_seconds: int = 300

    jwt_algorithm: str = "HS256"

    # Access token: short-lived, sent in the response body, stored by the
    # frontend (localStorage) and attached as an Authorization header.
    jwt_expire_minutes: int = 15

    # Refresh token: long-lived, sent ONLY as an httpOnly cookie (never
    # exposed to JS / never in a JSON response). Used solely to mint new
    # access tokens via POST /api/auth/refresh.
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "tt_refresh"

    # Whether the refresh cookie requires HTTPS (Secure flag). Disable for
    # local HTTP-only development; always enable in production.
    cookie_secure: bool = True

    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    media_dir: str = "data/media"

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.admin_bootstrap:
            if not self.admin_username or not self.admin_password:
                raise ValueError(
                    "ADMIN_BOOTSTRAP is True but ADMIN_USERNAME and/or "
                    "ADMIN_PASSWORD are not set."
                )
        if not self.jwt_secret:
            raise ValueError(
                "JWT_SECRET is required. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def app_version(self) -> str:
        """App version baked into the Docker image at build time.

        The value comes from the ``APP_VERSION`` environment variable set by the
        Dockerfile via ``ENV APP_VERSION=${APP_VERSION}``, which in turn is fed
        by the release pipeline's ``build-args`` (the git tag, e.g. ``2.1.11``).

        This is deliberately NOT a Pydantic field — it lives as a plain property
        so that ``.env`` files (whether loaded by Pydantic's dotenv support or by
        docker-compose's ``env_file`` directive) can NEVER override the build-time
        value.

        Falls back to ``"dev"`` for local development where no tag applies.
        """
        return os.environ.get("APP_VERSION", "dev")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
