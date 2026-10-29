"""A run's latency with the wait for a person removed (PRD N1/N2; Phase 6 chunk F reads this).

A run parked at the approval interrupt waits hours or days, and that is the approver's time, not the
system's. `run_waits` (migration 0036) records each wait: opened when the run parks, closed when the
decision arrives. `runs.latency_ms` is the ACTIVE time, derived by `end_run` in the statement
that ends the run; this read derives the same figure from the raw intervals so the two can be
checked against each other, and reports the wait beside it. A free-tier rate-limit wait inside
a model call is a different thing and stays in the active time: it is recorded per call
(`llm_calls.rate_limit_wait_ms`) so the evaluation can report latency with and without it
(Architecture.md section 4.3).

Evaluation organisations only, like every evaluation read.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection

RUN_ACTIVE_LATENCY_SQL = """
select r.id, r.arm, r.depth, r.status,
       (extract(epoch from (r.completed_at - r.created_at)) * 1000)::bigint as wall_ms,
       coalesce(sum(extract(epoch from (w.resumed_at - w.parked_at)) * 1000)
                filter (where w.resumed_at is not null), 0)::bigint as waited_ms,
       count(w.id) filter (where w.resumed_at is null)::int as open_waits,
       count(w.id)::int as waits,
       r.latency_ms
from public.runs r
join public.organisations o on o.id = r.org_id
left join public.run_waits w on w.run_id = r.id
where r.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and r.completed_at is not null
group by r.id
order by r.created_at, r.id
"""


@dataclass(frozen=True, slots=True)
class RunLatency:
    run_id: UUID
    arm: str
    depth: str | None
    status: str
    wall_ms: int
    waited_ms: int
    open_waits: int
    waits: int
    recorded_ms: int | None

    @property
    def active_ms(self) -> int:
        return max(0, self.wall_ms - self.waited_ms)


async def run_latencies(conn: AsyncConnection, org_ids: list[UUID]) -> list[RunLatency]:
    cur = await conn.execute(RUN_ACTIVE_LATENCY_SQL, {"org_ids": org_ids})
    return [RunLatency(*row) for row in await cur.fetchall()]
