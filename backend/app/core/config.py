"""Application settings, loaded once from the environment and the repository's `.env`.

Rules.md §3.1: pydantic-settings, loaded once; no `os.environ` reads anywhere else. Every other
module receives a `Settings` instance (or calls `get_settings()`), never the environment.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repository root, where `.env` lives (Architecture.md §11).
REPO_ROOT = Path(__file__).resolve().parents[3]

# Supavisor's transaction pooler. Architecture.md §11: session pooler only — the LangGraph
# checkpointer and psycopg's prepared statements need session semantics.
TRANSACTION_POOLER_PORT = 6543


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # `.env.example` ships `REDIS_URL=` etc.; an empty value means "use the default".
        env_ignore_empty=True,
        # `.env` also holds keys for later phases (GOOGLE_API_KEY, ...); they are not ours yet.
        extra="ignore",
        frozen=True,
        # A validation error must never echo DATABASE_URL: it carries the database password.
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # API process (runs on the laptop: Architecture.md §11, worker-host decision).
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # Redis: local Docker, AOF on (compose.yaml).
    # 127.0.0.1, not localhost: on Windows localhost tries ::1 first and takes ~2 s to fall back
    # (measured 2026-10-16), longer than ARQ's 1 s connect timeout. Redis is bound to IPv4 only.
    redis_url: RedisDsn = RedisDsn("redis://127.0.0.1:6379/0")
    # Per process. Each live SSE subscriber holds one connection for its blocking read.
    redis_max_connections: int = Field(default=32, ge=4)

    # Postgres through the Supavisor session pooler. Optional so that tests and CI, which have
    # no Supabase, can run; readiness reports it as not configured.
    database_url: SecretStr | None = None

    # Connection budget (Architecture.md §11): about 5 server connections in total across the
    # API, the ARQ worker and the LangGraph checkpointer, sized explicitly, never a library
    # default. The checkpointer arrives in Phase 6; its share is reserved now so that adding it
    # cannot silently exceed the budget.
    db_connection_budget: int = Field(default=5, ge=1)
    db_pool_api_max_size: int = Field(default=2, ge=1)
    db_pool_worker_max_size: int = Field(default=2, ge=1)
    db_pool_checkpointer_reserved: int = Field(default=1, ge=0)
    # Seconds a caller waits for a pooled connection before failing.
    db_pool_timeout_s: float = Field(default=10.0, gt=0)

    # ARQ worker. Concurrency kept low: one small shared Supabase instance (Architecture.md §11).
    worker_max_jobs: int = Field(default=2, ge=1)
    worker_job_timeout_s: int = Field(default=600, ge=1)

    # Run-progress event streams in Redis.
    run_events_ttl_s: int = Field(default=7 * 24 * 3600, ge=60)
    run_events_maxlen: int = Field(default=1000, ge=100)
    # Longest a blocking stream read waits before re-checking the run (milliseconds).
    run_events_block_ms: int = Field(default=5000, ge=100, le=60_000)

    # The fake 15-step run (Phases.md Phase 1). Never mounted in production.
    fake_runs_enabled: bool = True

    @field_validator("database_url")
    @classmethod
    def _session_pooler_only(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return None
        url = v.get_secret_value().strip()
        parts = urlsplit(url)
        if parts.scheme not in {"postgresql", "postgres"} or not parts.hostname:
            # Never echo the value: it carries the database password.
            raise ValueError("DATABASE_URL must be a postgresql:// URL with a host")
        if parts.port == TRANSACTION_POOLER_PORT:
            raise ValueError(
                "DATABASE_URL points at the transaction pooler (port 6543); use the session "
                "pooler on port 5432 (Architecture.md §11)"
            )
        return SecretStr(url)

    @model_validator(mode="after")
    def _within_connection_budget(self) -> "Settings":
        total = (
            self.db_pool_api_max_size
            + self.db_pool_worker_max_size
            + self.db_pool_checkpointer_reserved
        )
        if total > self.db_connection_budget:
            raise ValueError(
                f"database pools total {total} connections (api {self.db_pool_api_max_size}"
                f" + worker {self.db_pool_worker_max_size}"
                f" + checkpointer {self.db_pool_checkpointer_reserved}), over the budget of"
                f" {self.db_connection_budget} (Architecture.md §11)"
            )
        if self.environment == "production" and self.fake_runs_enabled:
            raise ValueError("fake_runs_enabled must be false in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read from the environment and `.env` exactly once."""
    return Settings()
