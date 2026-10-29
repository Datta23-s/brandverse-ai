"""Reading `llm_calls` when a graph step may have been run more than once (docs/evaluation-plan.md,
"Re-executed steps"; migration 0035).

The checkpointer is at-least-once: a worker that dies inside a step runs it again from the last
checkpoint. If that step made a model call, the log holds a row for the interrupted attempt (a
`pending` reservation that never finished, or a finished call whose result never reached the
checkpoint) AND a row for the retry. Both are real: the provider was asked twice, the tokens were
spent twice, the daily quota went down twice. Neither is a revision round (step 10 legitimately runs
up to three times in a run, each with its own `revision_round`) nor the gateway's repair call (the
same execution).

So every graph call carries (step, revision_round, execution_id), and an ATTEMPT is derived here:
within one (run, step, revision_round, agent), executions ranked by when each first called. The
two figures the evaluation needs are then both exact:

- SPEND (quota, tokens, cost, calls against a cap): every row. A resumed run spent twice.
- WORK (calls, tokens, cost and latency per campaign, per arm): the rows of the LAST attempt of each
  (run, step, revision_round, agent), the execution whose result the run used. The rows of earlier
  attempts are `resume_duplicates`, reported beside it and never silently dropped or silently kept.

A call made outside the graph has no step; it is its own work and its own spend.

`runs.llm_call_count` and `runs.total_cost_usd` are derived from every row (they are SPEND):
they are not the per-campaign work figure and must not be read as one.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection

# Evaluation organisations only, like every other evaluation read.
LLM_CALLS_BY_ATTEMPT_SQL = """
with executions as (
  select c.run_id, c.step, c.revision_round, coalesce(c.agent_id, '') as agent,
         c.execution_id, min(c.created_at) as started
  from public.llm_calls c
  join public.organisations o on o.id = c.org_id
  where c.org_id = any(%(org_ids)s)
    and not o.is_practice
    and not o.is_test
    and c.step is not null
  group by c.run_id, c.step, c.revision_round, coalesce(c.agent_id, ''), c.execution_id
), attempts as (
  select e.*,
         dense_rank() over (
           partition by e.run_id, e.step, e.revision_round, e.agent
           order by e.started, e.execution_id
         ) as attempt
  from executions e
), counted as (
  select a.*, max(a.attempt) over (
           partition by a.run_id, a.step, a.revision_round, a.agent
         ) as attempts
  from attempts a
)
select c.id, c.run_id, c.step, c.revision_round, c.agent_id, c.execution_id,
       coalesce(n.attempt, 1)::int as attempt,
       coalesce(n.attempt = n.attempts, true) as last_attempt,
       c.outcome, c.prompt_id, c.input_tokens, c.output_tokens, c.cost_usd::float8, c.latency_ms
from public.llm_calls c
join public.organisations o on o.id = c.org_id
left join counted n on n.run_id = c.run_id and n.execution_id = c.execution_id
  and n.step = c.step and n.revision_round = c.revision_round
  and n.agent = coalesce(c.agent_id, '')
where c.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
order by c.run_id, c.step, c.revision_round, c.agent_id, c.created_at, c.id
"""


@dataclass(frozen=True, slots=True)
class CallRow:
    id: int
    run_id: UUID
    step: int | None
    revision_round: int | None
    agent_id: str | None
    execution_id: UUID | None
    attempt: int
    last_attempt: bool
    outcome: str
    prompt_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None


@dataclass(frozen=True, slots=True)
class CallTotals:
    spend_calls: int
    spend_cost_usd: float
    work_calls: int
    work_cost_usd: float
    resume_duplicate_calls: int
    resume_duplicate_cost_usd: float


def totals(rows: Iterable[CallRow]) -> CallTotals:
    """Spend counts every call the provider may have been asked for (a refused call never reached
    it); work counts the last attempt's; the difference is reported, not dropped."""
    spend = [r for r in rows if r.outcome != "refused"]
    work = [r for r in spend if r.last_attempt]
    spend_cost = _cost(spend)
    work_cost = _cost(work)
    return CallTotals(
        spend_calls=len(spend),
        spend_cost_usd=spend_cost,
        work_calls=len(work),
        work_cost_usd=work_cost,
        resume_duplicate_calls=len(spend) - len(work),
        resume_duplicate_cost_usd=round(spend_cost - work_cost, 6),
    )


def _cost(rows: Iterable[CallRow]) -> float:
    return round(sum(r.cost_usd or 0.0 for r in rows), 6)


async def llm_calls_by_attempt(conn: AsyncConnection, org_ids: list[UUID]) -> list[CallRow]:
    cur = await conn.execute(LLM_CALLS_BY_ATTEMPT_SQL, {"org_ids": org_ids})
    return [CallRow(*row) for row in await cur.fetchall()]
