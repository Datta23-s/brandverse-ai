"""The Postgres checkpointer, on the one connection the connection budget reserves for it
(Architecture.md §11: API 2 + worker 2 + checkpointer 1).

The tables are made by migration 0034, in the private schema `graph_checkpoints`, not by the
library's `setup()` at run time (that would put a run's state in `public`, which the REST API
exposes). This
module points the connection at that schema and refuses to start if the installed library expects a
newer schema than the migration created: a library upgrade then needs a migration, and fails loudly
instead of writing to tables that are not there.

The connection must be a session-mode one (the Supavisor session pooler, never the transaction
pooler), autocommit, with dict rows: the saver's requirements. psycopg's async mode needs the
selector event loop on Windows (Architecture.md §11).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres import base as saver_base
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

SCHEMA = "graph_checkpoints"
LIBRARY_VERSION = len(saver_base.BasePostgresSaver.MIGRATIONS) - 1


class CheckpointSchemaOutdated(RuntimeError):
    pass


async def assert_checkpoint_schema(conn: AsyncConnection[dict[str, Any]]) -> None:
    """The database has every version the installed library expects, and no `setup()` is needed."""
    cur = await conn.execute("select max(v) as v from graph_checkpoints.checkpoint_migrations")
    row = await cur.fetchone()
    have = None if row is None else row["v"]
    if have != LIBRARY_VERSION:
        raise CheckpointSchemaOutdated(
            f"langgraph-checkpoint-postgres expects checkpoint schema version {LIBRARY_VERSION}; "
            f"{SCHEMA}.checkpoint_migrations has {have}. A library upgrade needs a new migration."
        )


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    conn = await AsyncConnection.connect(
        database_url,
        autocommit=True,
        row_factory=dict_row,
        application_name="brandverse-checkpointer",
    )
    try:
        await conn.execute("set search_path to graph_checkpoints")
        await assert_checkpoint_schema(conn)
        yield AsyncPostgresSaver(conn)
    finally:
        await conn.close()
