"""Redis connections for the API and the worker: one ARQ pool per process.

Jobs are serialised as JSON, not ARQ's default pickle: a job payload is a validated Pydantic model
dumped to JSON (Rules.md §3.1), and nothing read back from Redis is ever unpickled.
"""

import json
from dataclasses import replace
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.config import Settings


def json_serializer(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def json_deserializer(raw: bytes) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(raw)
    return data


def arq_redis_settings(settings: Settings) -> RedisSettings:
    return replace(
        RedisSettings.from_dsn(str(settings.redis_url)),
        max_connections=settings.redis_max_connections,
    )


async def create_arq_pool(settings: Settings) -> ArqRedis:
    """Connect (ARQ retries a few times) and return the process's Redis pool."""
    return await create_pool(
        arq_redis_settings(settings),
        job_serializer=json_serializer,
        job_deserializer=json_deserializer,
    )
