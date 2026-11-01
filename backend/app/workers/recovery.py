"""Finding the runs a dead worker left behind, and continuing them (Phases.md chunk F).

`Pipeline.recover` could always continue a run from its checkpoint, but NOTHING called it: a worker
killed at campaign 60 of 100 left that run `queued` for ever unless a person ran it by hand. This is
the trigger: when a worker starts, runs that have a checkpoint and never reached an ending are
continued by a `recover_pipeline` job.

ASSUMPTION, stated: **one worker process.** A worker that has just started is taken to mean the
previous one died. With two workers this sweep would continue a run the other is still executing;
the project runs one (Architecture.md section 11, the laptop is the worker host), and a second
worker needs a lease on the run before this is safe.
"""

import logging
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

# A run that started (it has a checkpoint) and has no ending: `queued` is the status it holds
# from creation until `suspend_run` (parked: waiting for a person, not for a worker) or `end_run`.
ORPHANED_RUNS_SQL = """
select r.id
from public.runs r
where r.status = 'queued'
  and exists (select 1 from graph_checkpoints.checkpoints c where c.thread_id = r.id::text)
order by r.created_at
"""


# A call row is 'pending' from admission until the provider answers. With ONE worker (above), a
# worker that has just started means no call is in flight, so every 'pending' row is the remains
# of a dead process: it may have reached the provider, so it stays in SPEND, but it is not WORK.
ORPHAN_PENDING_SQL = """
update public.llm_calls set outcome = 'orphaned', finished_at = now()
where outcome = 'pending'
"""


async def orphan_pending_calls(pool: AsyncConnectionPool[AsyncConnection]) -> int:
    """Move every `pending` call to the terminal `orphaned` state (0045). Returns how many.
    Spend keeps them (quota, call caps, the cost report); work excludes them
    (`app/evaluation/llm_attempts.py`), so a crash cannot inflate ARR."""
    async with pool.connection() as conn:
        cur = await conn.execute(ORPHAN_PENDING_SQL)
        return cur.rowcount


async def orphaned_runs(pool: AsyncConnectionPool[AsyncConnection]) -> list[UUID]:
    async with pool.connection() as conn:
        cur = await conn.execute(ORPHANED_RUNS_SQL)
        return [row[0] for row in await cur.fetchall()]
