"""Shared fixtures: the async backend, and an isolated Redis for `@pytest.mark.redis` tests.

Async tests run on a `SelectorEventLoop`, the loop the API and worker run on (psycopg needs it).

Redis tests use REDIS_TEST_URL (default `redis://127.0.0.1:6379/15`) and flush that database before
each test, so they refuse database 0, where the development queue lives. With
REQUIRE_REDIS_TESTS=1 (set in CI) an unreachable Redis is a failure, not a skip.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import pytest
from redis.asyncio import Redis

from app.core.config import Settings

DEFAULT_REDIS_TEST_URL = "redis://127.0.0.1:6379/15"


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


def make_settings(**overrides: Any) -> Settings:
    """Settings for tests: never read `.env`, never point at Supabase."""
    values: dict[str, Any] = {"environment": "test", "database_url": None}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
async def redis_settings() -> AsyncIterator[Settings]:
    url = os.environ.get("REDIS_TEST_URL", DEFAULT_REDIS_TEST_URL)
    db = urlsplit(url).path.lstrip("/") or "0"
    if db == "0":
        pytest.exit("REDIS_TEST_URL must not use database 0 (the development queue)", returncode=4)

    client = Redis.from_url(url)
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        if os.environ.get("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail(f"REQUIRE_REDIS_TESTS=1 but Redis at {url} is unreachable: {e}")
        pytest.skip(f"Redis at {url} unreachable; start it with `docker compose up -d redis`")
    await client.flushdb()
    try:
        yield make_settings(redis_url=url, run_events_block_ms=200)
    finally:
        await client.flushdb()
        await client.aclose()
