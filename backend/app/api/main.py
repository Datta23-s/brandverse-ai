"""The FastAPI application: `create_app()` and the process entry point.

Runs on the laptop (Architecture.md §11, worker-host decision). Start it with
`uv run python -m app.api` from `backend/`, with Redis up (`docker compose up -d redis`).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api import health, runs
from app.api.deps import Resources
from app.core.config import Settings, get_settings
from app.core.db import create_db_pool
from app.core.logging import configure_logging
from app.core.redis import create_arq_pool
from app.core.run_events import RunEventLog

log = logging.getLogger(__name__)

# psycopg's async mode refuses Windows' default ProactorEventLoop, which uvicorn picks on Windows.
# A custom `loop=` import string is used by uvicorn as the loop constructor itself.
UVICORN_LOOP = "asyncio:SelectorEventLoop"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = await create_arq_pool(settings)
        db_pool = None
        if settings.database_url is not None:
            db_pool = create_db_pool(settings, "api")
            await db_pool.open()
        else:
            log.warning("DATABASE_URL not set: API database pool disabled")
        app.state.resources = Resources(
            settings=settings,
            redis=redis,
            events=RunEventLog(
                redis,
                ttl_s=settings.run_events_ttl_s,
                maxlen=settings.run_events_maxlen,
                block_ms=settings.run_events_block_ms,
            ),
            db_pool=db_pool,
        )
        log.info(
            "api started",
            extra={
                "db_pool_max_size": settings.db_pool_api_max_size if db_pool else 0,
                "fake_runs_enabled": settings.fake_runs_enabled,
            },
        )
        try:
            yield
        finally:
            if db_pool is not None:
                await db_pool.close()
            await redis.aclose(close_connection_pool=True)

    app = FastAPI(title="BrandverseAI API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    # The stream endpoint is the real pipeline's too; only the fake trigger is gated.
    app.include_router(runs.router)
    if settings.fake_runs_enabled:
        app.include_router(runs.fake_router)
    return app


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        loop=UVICORN_LOOP,
        # Our JSON logging, not uvicorn's dictConfig (Rules.md P12).
        log_config=None,
    )
