"""Drive the graph to its next stopping point, and END THE RUN THE WAY THE EXECUTOR DOES.

`budgeted_run.run_steps` is a linear loop and cannot host a graph with a fan-out, a loop and an
interrupt, but its endings are what Architecture.md §4.4 binds Phase 6 to: this module reuses them
(`end_run`, `suspend_run`, `fail_run`) and writes no status of its own.

- stopped at the approval interrupt: the run is parked `awaiting_approval` and a step-12 `started`
  event says so; the event stream stays open (it is not terminal);
- every step ran: `completed`, with everything staged. A REJECTED campaign is a completed run too:
  step 15 stages nothing that adds trust for it (`write_memory`), so only corrections can commit;
- a gate blocked it at step 3: `blocked` (0015), a result in its own right;
- a budget refusal (`BudgetExceeded`): `partial`, `budget_exhausted`, corrections only
  (Rules.md C4);
- any other error: `failed`, corrections committed, re-raised.
- cancellation: NOTHING is written and it propagates. The checkpoint is the run's record, so the run
  stays resumable; a worker that is killed outright writes nothing either, for the same reason.

LIMITATION, stated: the event contract has no "partial" ending. A partial run emits `run.completed`
after the failed step event; telling the two apart in the stream needs a field on `RunCompleted` and
the matching TypeScript contract (chunk E).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import end_run, fail_run, suspend_run
from app.core.infrastructure import classify_infrastructure_failure
from app.core.pipeline import PIPELINE_STEPS
from app.errors import BrandverseError, BudgetExceeded
from app.memory.writeback import MemoryStage, StagedMemory
from app.models.runs import RunCompleted, RunFailed, RunStarted, StepEvent, StepStatus
from app.orchestrator.deps import EventSink
from app.orchestrator.graph import APPROVAL_NODE
from app.orchestrator.state import Decision, Depth, RunState

log = logging.getLogger(__name__)

APPROVAL_STEP = 12
TRUTH_STEP = 3


class NotAwaitingApproval(BrandverseError):
    """A decision was offered for a run that is not parked at the approval interrupt."""


class NotRecoverable(BrandverseError):
    """`recover` was asked for a run that has finished, never started, or is parked for a person."""


class RunLedger(Protocol):
    """How a run's status is written: the executor's functions, behind a seam a test can fake."""

    async def suspend(self, run_id: UUID) -> None: ...

    async def end(
        self,
        run_id: UUID,
        status: Literal["completed", "partial", "blocked"],
        *,
        budget_exhausted: bool,
        memory: StagedMemory | None,
    ) -> None: ...

    async def fail(self, run_id: UUID, corrections: StagedMemory | None) -> None: ...


class PoolRunLedger:
    def __init__(self, pool: AsyncConnectionPool[AsyncConnection]) -> None:
        self._pool = pool

    async def suspend(self, run_id: UUID) -> None:
        await suspend_run(self._pool, run_id)

    async def end(
        self,
        run_id: UUID,
        status: Literal["completed", "partial", "blocked"],
        *,
        budget_exhausted: bool,
        memory: StagedMemory | None,
    ) -> None:
        await end_run(self._pool, run_id, status, budget_exhausted=budget_exhausted, memory=memory)

    async def fail(self, run_id: UUID, corrections: StagedMemory | None) -> None:
        await fail_run(self._pool, run_id, corrections)


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    status: Literal["suspended", "completed", "partial", "blocked"]
    state: dict[str, Any]
    reason: str | None = None


OnBlocked = Callable[[RunState], Awaitable[None]]
Graph = CompiledStateGraph[RunState, Any, RunState, RunState]


class Pipeline:
    def __init__(
        self,
        graph: Graph,
        events: EventSink,
        ledger: RunLedger,
        *,
        max_revisions: int,
        on_blocked: OnBlocked | None = None,
    ) -> None:
        self._graph, self._events, self._ledger = graph, events, ledger
        self._max_revisions, self._on_blocked = max_revisions, on_blocked

    async def start(
        self,
        *,
        run_id: UUID,
        org_id: UUID,
        user_id: UUID,
        product_id: UUID,
        brief: str | None = None,
        depth: Depth = "D1",
    ) -> PipelineOutcome:
        await self._events.append(RunStarted(run_id=run_id))
        initial = RunState(
            run_id=str(run_id),
            org_id=str(org_id),
            user_id=str(user_id),
            product_id=str(product_id),
            brief=brief,
            depth=depth,
            request={"brief": brief, "depth": depth},
        )
        return await self._drive(run_id, initial)

    async def resume(
        self, run_id: UUID, decision: Decision, *, by: UUID, reason: str | None = None
    ) -> PipelineOutcome:
        """Continue a parked run with the approver's decision. State comes from the checkpoint, not
        from this process: any worker can do this, days later."""
        config = self._config(run_id, MemoryStage())
        snapshot = await self._graph.aget_state(config)
        if tuple(snapshot.next) != (APPROVAL_NODE,):
            raise NotAwaitingApproval(f"run {run_id} is not waiting for approval")
        if decision != "approve" and not reason:
            raise ValueError("a reason is required for anything but approve")
        if decision == "revise" and snapshot.values.get("revision_round", 0) >= self._max_revisions:
            raise ValueError(f"no regeneration rounds left (limit {self._max_revisions})")
        await self._graph.aupdate_state(
            config,
            {"approval": {"decision": decision, "by": str(by), "reason": reason}},
            as_node="validate_evidence",
        )
        return await self._drive(run_id, None)

    async def recover(self, run_id: UUID) -> PipelineOutcome:
        """Continue a run whose worker died mid-run, from its last checkpoint and not from the
        start. Steps that finished are not run again (a finished agent of a parallel step keeps its
        saved result); the step that was running when the worker died is (at-least-once, nodes.py).
        A run parked at the interrupt is waiting for a person, not for a worker: use `resume`."""
        snapshot = await self._graph.aget_state(self._config(run_id, MemoryStage()))
        if not snapshot.next:
            raise NotRecoverable(f"run {run_id} has no checkpoint to continue, or has finished")
        if tuple(snapshot.next) == (APPROVAL_NODE,):
            raise NotRecoverable(f"run {run_id} is waiting for approval: use resume")
        return await self._drive(run_id, None)

    # ── internals ─────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _config(run_id: UUID, stage: MemoryStage) -> RunnableConfig:
        # A fresh stage per invocation: it lives in this process only (nodes.py).
        return {"configurable": {"thread_id": str(run_id), "stage": stage}}

    async def _drive(self, run_id: UUID, graph_input: RunState | None) -> PipelineOutcome:
        stage = MemoryStage()
        config = self._config(run_id, stage)
        try:
            values = cast(dict[str, Any], await self._graph.ainvoke(graph_input, config))
            snapshot = await self._graph.aget_state(config)
            if snapshot.next:
                return await self._park(run_id, values)
            if values.get("blocked"):
                return await self._blocked(run_id, values)
            await self._ledger.end(run_id, "completed", budget_exhausted=False, memory=stage.all())
            await self._events.append(RunCompleted(run_id=run_id))
            return PipelineOutcome("completed", values)
        except BudgetExceeded as e:
            await self._ledger.end(
                run_id, "partial", budget_exhausted=True, memory=stage.corrections_only()
            )
            await self._events.append(RunCompleted(run_id=run_id))
            return PipelineOutcome("partial", {}, str(e))
        except asyncio.CancelledError:
            raise  # nothing is written: the checkpoint is the record, and the run stays resumable
        except BaseException as e:
            infrastructure = classify_infrastructure_failure(e)
            corrections = stage.corrections_only() if infrastructure is None else None
            log.warning("run failed", extra={"infrastructure_failure": infrastructure})
            await self._ledger.fail(run_id, corrections)
            try:
                await self._events.append(
                    RunFailed(run_id=run_id, error=f"internal error: {type(e).__name__}")
                )
            except Exception:
                log.exception("the failure could not be reported on the stream")
            raise

    async def _park(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        await self._events.append(
            StepEvent(
                run_id=run_id,
                step=APPROVAL_STEP,
                name=PIPELINE_STEPS[APPROVAL_STEP - 1].name,
                status=StepStatus.STARTED,
                detail="awaiting approval",
            )
        )
        await self._ledger.suspend(run_id)
        return PipelineOutcome("suspended", values)

    async def _blocked(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        reasons = values["blocked"]
        if self._on_blocked is not None:
            try:
                await self._on_blocked(cast(RunState, values))
            except Exception:
                # The run stays blocked; a lost audit row is an error to see, not a reason to admit.
                log.exception("run block could not be audited")
        await self._ledger.end(run_id, "blocked", budget_exhausted=False, memory=None)
        message = "blocked: " + "; ".join(r["code"] for r in reasons)
        await self._events.append(RunFailed(run_id=run_id, step=TRUTH_STEP, error=message))
        return PipelineOutcome("blocked", values, message)
