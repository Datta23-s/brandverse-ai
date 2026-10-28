"""Application settings, loaded once from the environment and the repository's `.env`.

Rules.md §3.1: pydantic-settings, loaded once; no `os.environ` reads anywhere else. Every other
module receives a `Settings` instance (or calls `get_settings()`), never the environment.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, HttpUrl, RedisDsn, SecretStr, field_validator, model_validator
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
        # `.env` also holds keys for later phases; they are not ours yet.
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

    # Supabase Auth. Access tokens are ES256 JWTs verified locally against the project's JWKS
    # (Architecture.md §11, "Authentication"); the API never asks Supabase per request.
    supabase_url: HttpUrl | None = None
    jwt_audience: str = "authenticated"
    # Re-read the key set at least this often, to pick up a rotation proactively.
    jwks_ttl_s: int = Field(default=3600, ge=60)
    # An unknown `kid` triggers one refetch, at most this often (a flood of bad tokens must not
    # hammer Supabase).
    jwks_min_refetch_interval_s: float = Field(default=30.0, ge=1.0)
    # Clock skew tolerated on exp/iat.
    jwt_leeway_s: int = Field(default=30, ge=0, le=300)
    # Safe in a browser (RLS applies); scripts use it to sign in like the frontend does.
    supabase_publishable_key: str | None = None
    # Backend-only, bypasses RLS: used by local scripts (e2e test users), never by a request
    # path and never in frontend code (Rules.md P2).
    supabase_secret_key: SecretStr | None = None

    # ── LLM calls (Phase 2) ──────────────────────────────────────────────────────────────────
    # The model registry: the only place a model ID appears (Rules.md P5).
    models_config_path: Path = REPO_ROOT / "config" / "models.yaml"
    # Versioned prompt templates and their lock (app/llm/prompts.py).
    prompts_dir: Path = REPO_ROOT / "config" / "prompts"
    google_api_key: SecretStr | None = None
    # Per-run hard caps (Rules.md C3), enforced from llm_calls before every call.
    max_llm_calls_per_run: int = Field(default=20, ge=1)
    max_cost_per_run_usd: float = Field(default=2.50, gt=0)
    llm_call_timeout_s: float = Field(default=60.0, gt=0)
    # Rate-limit waits (Rules.md C10) are capped: no wait loop runs unbounded against a model.
    llm_max_rate_limit_waits: int = Field(default=3, ge=0, le=10)
    llm_max_rate_limit_wait_s: float = Field(default=90.0, gt=0, le=600)
    # A 'pending' llm_calls row older than this is an abandoned reservation (its process died
    # between admission and finish) and stops counting toward caps and quotas. It must exceed the
    # longest a live call can stay pending — (waits + 1) x timeout + total wait = 330 s at the
    # defaults — or an in-flight call would be miscounted as abandoned; checked below.
    llm_pending_expiry_s: int = Field(default=900, ge=60)
    # The strong tier (20 requests/day, shared with the evaluation) is REFUSED unless this is set.
    # Development, tests and smoke calls never reach it; set it only for evaluation runs.
    llm_allow_strong_tier: bool = False
    # Narrows the line above (owner, 2026-10-28): when set, the strong tier is allowed for THIS
    # organisation only, and only while the organisation is flagged `is_practice`, so a tuning
    # organisation can measure the strong calls a run takes without any other organisation, or any
    # evaluation read, being able to spend or see them. Set, it overrides `llm_allow_strong_tier`.
    llm_strong_tier_org_id: UUID | None = None

    # ── Research (Phase 5) ───────────────────────────────────────────────────────────────────
    # The self-hosted SearXNG JSON API on the laptop (Architecture.md §11). Unset means search is
    # unavailable and research degrades to a partial result.
    searxng_url: HttpUrl | None = None
    search_timeout_s: float = Field(default=10.0, gt=0)

    # Browser origins allowed to call the API: the local Next.js app (Phases.md Phase 1, amended).
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

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
        longest_pending_s = (
            self.llm_max_rate_limit_waits + 1
        ) * self.llm_call_timeout_s + self.llm_max_rate_limit_wait_s
        if self.llm_pending_expiry_s <= longest_pending_s:
            raise ValueError(
                f"llm_pending_expiry_s ({self.llm_pending_expiry_s}) must exceed the longest a "
                f"call can stay pending ({longest_pending_s:.0f} s), or live calls would be "
                "counted as abandoned"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read from the environment and `.env` exactly once."""
    return Settings()
