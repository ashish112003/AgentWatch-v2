"""
app/core/config.py
──────────────────
Centralised configuration using Pydantic Settings v2.

All environment variables are declared here with types and defaults.
Pydantic automatically reads from the .env file AND the OS environment,
so the app never needs scattered os.getenv() calls.

Usage anywhere in the codebase:
    from app.core.config import settings
    print(settings.GROQ_API_KEY)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # ── Pydantic Settings config ─────────────────────────────────────
    # model_config tells Pydantic where to find the .env file and how
    # to handle case sensitivity (env vars are case-insensitive by default).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Groq / AI ────────────────────────────────────────────────────
    GROQ_API_KEY: str = "not_set"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── JWT ──────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "dev_secret_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Database ─────────────────────────────────────────────────────
    # Accepts any SQLAlchemy async URL.  Examples:
    #   SQLite (dev):    sqlite+aiosqlite:///./agentwatch.db
    #   PostgreSQL:      postgresql+asyncpg://user:pw@host:5432/db
    # When DATABASE_URL is not set, individual POSTGRES_* vars are used
    # to construct a PostgreSQL URL automatically (see db/database.py).
    DATABASE_URL: str = "sqlite+aiosqlite:///./agentwatch.db"

    # ── PostgreSQL (optional — used only when DATABASE_URL starts with postgresql) ──
    # These are ignored when DATABASE_URL is set to a SQLite URL.
    # In Docker, set these via environment variables rather than DATABASE_URL
    # so the password is not embedded in a URL string in docker-compose.yml.
    POSTGRES_HOST:     str = "localhost"
    POSTGRES_PORT:     int = 5432
    POSTGRES_DB:       str = "agentwatch"
    POSTGRES_USER:     str = "agentwatch"
    POSTGRES_PASSWORD: str = ""   # REQUIRED in production — set via env var

    @property
    def postgres_url(self) -> str:
        """Construct an asyncpg URL from individual POSTGRES_* settings."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def effective_database_url(self) -> str:
        """
        Return the database URL to use.

        Priority:
          1. DATABASE_URL if it points to PostgreSQL (starts with 'postgresql')
          2. postgres_url if DATABASE_URL is unset / still the default SQLite value
             AND POSTGRES_PASSWORD is configured.
          3. DATABASE_URL as-is (SQLite default for development).
        """
        if self.DATABASE_URL.startswith("postgresql"):
            return self.DATABASE_URL
        if self.POSTGRES_PASSWORD:
            return self.postgres_url
        return self.DATABASE_URL

    # ── App ──────────────────────────────────────────────────────────
    ALLOW_ORIGINS: str = ""       # Comma-separated allowed origins, e.g. "https://app.example.com"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache()
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.

    Using lru_cache means the .env file is read exactly once at startup,
    not on every import.  Tests can override this by calling
    get_settings.cache_clear() before patching environment variables.
    """
    return Settings()


# Module-level singleton — import this throughout the app.
settings = get_settings()

# H3: warn at import time if the default secret is still in use.
# This is the only acceptable place for a print() call — it fires once
# at startup so the warning is visible in container logs.
import sys as _sys
if settings.JWT_SECRET_KEY == "dev_secret_change_in_production" and settings.is_production:
    print(
        "SECURITY WARNING: JWT_SECRET_KEY is set to the default development value. "
        "Set a strong random secret in .env before deploying.",
        file=_sys.stderr,
    )
    raise RuntimeError("Cannot start in production with default JWT_SECRET_KEY.")