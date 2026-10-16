"""Liveness and readiness.

`/health` answers 200 whenever the process is serving, with no dependency checks (Phases.md
Phase 1: "`/health` returns 200"). `/health/ready` checks Redis and the database and answers 503
if either is unreachable or the database is not configured.
"""

import asyncio
import logging
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import ResourcesDep
from app.core.db import ping_database

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_S = 5.0


class CheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    redis: CheckStatus
    database: CheckStatus


@router.get("/health")
async def health() -> Liveness:
    return Liveness()


@router.get(
    "/health/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def ready(resources: ResourcesDep, response: Response) -> Readiness:
    redis_status = CheckStatus.OK
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT_S):
            await resources.redis.ping()
    except Exception:
        log.exception("readiness: redis unreachable")
        redis_status = CheckStatus.ERROR

    if resources.db_pool is None:
        db_status = CheckStatus.NOT_CONFIGURED
    else:
        db_status = CheckStatus.OK
        try:
            async with asyncio.timeout(_CHECK_TIMEOUT_S):
                await ping_database(resources.db_pool)
        except Exception:
            log.exception("readiness: database unreachable")
            db_status = CheckStatus.ERROR

    is_ready = redis_status is CheckStatus.OK and db_status is CheckStatus.OK
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status="ready" if is_ready else "not_ready", redis=redis_status, database=db_status
    )
