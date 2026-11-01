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
       r.latency_ms,
       (select coalesce(sum(c.latency_ms), 0) from public.llm_calls c
        where c.run_id = r.id and c.outcome <> 'refused')::bigint as model_ms,
       r.timings
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
    model_ms: int = 0
    timings: dict[str, float] | None = None

    @property
    def active_ms(self) -> int:
        return max(0, self.wall_ms - self.waited_ms)

    def components(self) -> dict[str, float | None]:
        """The run's active time in parts (owner, 2026-11-02), each recorded by what measured it:

        - `model_ms`: the model latency SUMMED over the run's calls (`llm_calls`). Agents that run
          at the same time overlap, so this can exceed the wall time they took together.
        - `search_ms`: the time inside the search provider's calls (about zero under replay;
          recorded anyway so a live run is comparable).
        - `research_other_ms`: the rest of the research step (replayed fetches, extraction,
          scoring).
        - `encoder_recall_ms`, `encoder_writeback_ms`: local embedding, measured separately.
        - `waits_ms`: the waits for a person (`run_waits`), already outside the active time.
        - `residual_ms`: active time minus the five parts above. SIGNED and never clamped: a
          positive residual is time nothing measured (database round trips, graph overhead, other
          local compute); a negative one is the overlap of concurrent model calls. It is named, not
          folded into any other figure.

        A run with no `timings` (before migration 0044, or a run that ended before it was written)
        has None for every local part and for the residual: unknown, not zero."""
        t = self.timings
        parts: dict[str, float | None] = {
            "model_ms": float(self.model_ms),
            "search_ms": None if t is None else t.get("search_ms", 0.0),
            "research_other_ms": None if t is None else t.get("research_other_ms", 0.0),
            "encoder_recall_ms": None if t is None else t.get("encoder_recall_ms", 0.0),
            "encoder_writeback_ms": None if t is None else t.get("encoder_writeback_ms", 0.0),
            "waits_ms": float(self.waited_ms),
        }
        active = self.recorded_ms if self.recorded_ms is not None else self.active_ms
        if t is None:
            parts["residual_ms"] = None
        else:
            named = sum(v or 0.0 for k, v in parts.items() if k not in ("waits_ms", "residual_ms"))
            parts["residual_ms"] = float(active) - named
        return parts


async def run_latencies(conn: AsyncConnection, org_ids: list[UUID]) -> list[RunLatency]:
    cur = await conn.execute(RUN_ACTIVE_LATENCY_SQL, {"org_ids": org_ids})
    return [RunLatency(*row) for row in await cur.fetchall()]
