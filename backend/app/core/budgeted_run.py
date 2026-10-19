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

**Memory write-back is staged, then committed at termination (2026-10-17).** Whether an attempt
is the primary result is known only when it ends (docs/evaluation-plan.md §2: the first attempt
that terminates without infrastructure failure), and memory_items rows can never be deleted
(0010, 0011) — so nothing may reach memory_items before the end. Steps never write memory
themselves: they append to the run's `MemoryStage`, which exists only in this process. The stage
is inserted in **the same transaction** that writes the run's terminal status, and only when the
run ends `completed` (and, for an experiment run, when the Phase 10 primacy check passes inside
that transaction). Every other ending — `partial`, `failed`, an infrastructure failure
(app/core/infrastructure.py), cancellation — discards it. A process that dies takes the stage with
it: it was never anywhere durable. What is eligible to be staged, and what the commit checks
(human approval, the claim gate, near-duplicates; Memory.md §6.1), is app/memory/writeback.py's
business, not this executor's: `end_run` hands it the stage inside the terminal transaction.
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

from app.core.infrastructure import classify_infrastructure_failure
from app.errors import BudgetExceeded
from app.memory.writeback import Committed, MemoryStage, MemoryWrite, commit_memory

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
    memory: Sequence[MemoryWrite] = (),
) -> Committed | None:
    """Write the run's terminal status — and, for a completed run only, its staged memory — in one
    transaction: both land or neither does."""
    if memory and status != "completed":
        raise ValueError("staged memory is committed only for a completed run")
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            _END_RUN_SQL, {"status": status, "exhausted": budget_exhausted, "run": run_id}
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"run {run_id} not found")
        if memory:
            return await commit_memory(conn, run_id, memory)
    return None


async def run_steps(
    steps: Sequence[Step],
    *,
    pool: AsyncConnectionPool[AsyncConnection],
    run_id: UUID,
    memory: MemoryStage | None = None,
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
                # A partial run is a result, but it never reached write-back: stage discarded.
                await end_run(pool, run_id, "partial", budget_exhausted=True)
                return RunOutcome("partial", outputs, not_run, True, str(e))
        # Inside the try: if committing the status and the staged memory fails, the run ends
        # 'failed' below rather than being left unterminated.
        await end_run(
            pool,
            run_id,
            "completed",
            budget_exhausted=False,
            memory=memory.items if memory else (),
        )
    except BaseException as e:
        # Cancellation included: mark the run failed, discard staged memory, then propagate.
        log.warning(
            "run failed; staged memory discarded",
            extra={
                "run_id": str(run_id),
                "infrastructure_failure": classify_infrastructure_failure(e),
                "staged_memory": len(memory.items) if memory else 0,
            },
        )
        await asyncio.shield(end_run(pool, run_id, "failed", budget_exhausted=False))
        raise
    return RunOutcome("completed", outputs, [], False, None)
