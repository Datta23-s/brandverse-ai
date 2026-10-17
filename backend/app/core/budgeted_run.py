"""A run's LLM steps under its budget: a breach ends the run as a PARTIAL result flagged
`budget_exhausted` (Rules.md C4) — never a crash, never a silent truncation.

Steps run in order; each may make any number of gateway calls and sees the outputs before it.
When the ledger refuses a call because the run's caps would be breached (BudgetExceeded, Rules.md
C3), the run stops there: the outputs of the steps that completed are returned, the steps that
did not run are named, and the runs row is marked `status = 'partial'`, `budget_exhausted =
true`. The refusal itself is already in llm_calls with its reason (app/llm/ledger.py).

Run statuses written here: 'completed', 'partial' (stopped by the budget, with output),
'failed' (any other error; re-raised). `end_run` also writes 'blocked', for a run a truth or safety
gate stopped (migration 0015; app/core/admission.py) — reported per arm as a result in its own
right (app/evaluation/run_outcomes.py). `llm_call_count` and `total_cost_usd` on the runs row are
derived from llm_calls when the run ends — one query, never a counter kept alongside (Rules.md
A1); `total_cost_usd` is rounded to the column's 4 decimals, so the raw log stays the source.

This is the budget behaviour the Phase 6 LangGraph pipeline inherits (Phases.md Phase 2 exit
criterion); the graph will call `run_steps`' parts, not reimplement them. An exhausted daily
quota (QuotaExhausted) is not a budget breach: it is meant to downgrade or defer (Rules.md C9),
which is not built yet, so here it fails the run.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.errors import BudgetExceeded

log = logging.getLogger(__name__)

StepFn = Callable[[Mapping[str, BaseModel]], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    run: StepFn


@dataclass(frozen=True, slots=True)
class RunOutcome:
    status: Literal["completed", "partial"]
    outputs: dict[str, BaseModel]  # completed steps, in order
    not_run: list[str]  # the step the budget stopped, and every step after it
    budget_exhausted: bool
    reason: str | None


_END_RUN_SQL = """
update public.runs r
set status = %(status)s, budget_exhausted = %(exhausted)s, completed_at = now(),
    llm_call_count = u.calls, total_cost_usd = u.cost
from (select count(*) filter (where outcome <> 'refused')::int as calls,
             coalesce(sum(cost_usd), 0) as cost
      from public.llm_calls where run_id = %(run)s) u
where r.id = %(run)s
"""


async def end_run(
    pool: AsyncConnectionPool[AsyncConnection],
    run_id: UUID,
    status: Literal["completed", "partial", "failed", "blocked"],
    *,
    budget_exhausted: bool,
) -> None:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            _END_RUN_SQL, {"status": status, "exhausted": budget_exhausted, "run": run_id}
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"run {run_id} not found")


async def run_steps(
    steps: Sequence[Step], *, pool: AsyncConnectionPool[AsyncConnection], run_id: UUID
) -> RunOutcome:
    if len({s.name for s in steps}) != len(steps):
        raise ValueError("step names must be unique")
    outputs: dict[str, BaseModel] = {}
    try:
        for i, step in enumerate(steps):
            try:
                outputs[step.name] = await step.run(outputs)
            except BudgetExceeded as e:
                not_run = [s.name for s in steps[i:]]
                log.warning(
                    "run budget exhausted; partial result",
                    extra={"run_id": str(run_id), "step": step.name, "reason": str(e)},
                )
                await end_run(pool, run_id, "partial", budget_exhausted=True)
                return RunOutcome("partial", outputs, not_run, True, str(e))
    except BaseException:
        # Cancellation included: mark the run failed, then propagate.
        await asyncio.shield(end_run(pool, run_id, "failed", budget_exhausted=False))
        raise
    await end_run(pool, run_id, "completed", budget_exhausted=False)
    return RunOutcome("completed", outputs, [], False, None)
