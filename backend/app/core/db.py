"""Postgres connection pools, sized to the connection budget (Architecture.md §11).

The laptop reaches Supabase only through the Supavisor session pooler, where every client
connection holds one server connection for its whole life, out of `max_connections = 60` shared
with Supabase's own services. So: one pool per process, sized explicitly from `Settings` (the
budget is validated there), `min_size = 0` so an idle process holds no server connection, and
agents share their process's pool rather than opening their own.
"""

from typing import Literal

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings

PoolRole = Literal["api", "worker"]

# Close connections idle this long, returning them to the shared instance.
_MAX_IDLE_S = 120.0


def pool_max_size(settings: Settings, role: PoolRole) -> int:
    return settings.db_pool_api_max_size if role == "api" else settings.db_pool_worker_max_size


def create_db_pool(settings: Settings, role: PoolRole) -> AsyncConnectionPool[AsyncConnection]:
    """An unopened pool for `role`. Caller opens it (`await pool.open()`) and closes it."""
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is not configured")
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=0,
        max_size=pool_max_size(settings, role),
        open=False,
        name=f"brandverse-{role}",
        timeout=settings.db_pool_timeout_s,
        max_idle=_MAX_IDLE_S,
        # Supavisor may drop idle client connections; check before handing one out.
        check=AsyncConnectionPool.check_connection,
        kwargs={"application_name": f"brandverse-{role}"},
    )


async def ping_database(pool: AsyncConnectionPool[AsyncConnection]) -> None:
    """Round-trip `select 1`. Raises on failure."""
    async with pool.connection() as conn:
        await conn.execute("select 1")
