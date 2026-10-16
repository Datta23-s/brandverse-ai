"""Request dependencies: process-wide resources created once in the app's lifespan."""

from dataclasses import dataclass
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.run_events import RunEventLog


@dataclass(frozen=True, slots=True)
class Resources:
    settings: Settings
    redis: ArqRedis
    events: RunEventLog
    db_pool: AsyncConnectionPool[AsyncConnection] | None


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]
