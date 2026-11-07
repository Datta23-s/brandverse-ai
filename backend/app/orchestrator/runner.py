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

A partial run emits `run.completed` with `partial = true` (chunk E), so a stream reader can tell it
from a complete run; a rejected campaign is a complete run (`partial = false`).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import RunFacts, end_run, fail_run, resume_run, suspend_run
from app.core.infrastructure import classify_infrastructure_failure
from app.core.pipeline import PIPELINE_STEPS
from app.errors import BrandverseError, BudgetExceeded, ProviderCircuitOpen, StepTimeout
from app.memory.necessity import DepthPolicy
from app.memory.writeback import MemoryStage, StagedMemory
from app.models.runs import RunCompleted, RunFailed, RunStarted, StepEvent, StepStatus
from app.orchestrator import progress
from app.orchestrator.deps import EventSink
from app.orchestrator.graph import APPROVAL_NODE
from app.orchestrator.state import Arm, Decision, Depth, RunState

log = logging.getLogger(__name__)

APPROVAL_STEP = 12
TRUTH_STEP = 3


class NotAwaitingApproval(BrandverseError):
    """A decision was offered for a run that is not parked at the approval interrupt."""


class EditOutRefused(BrandverseError):
    """The words an edit-out names cannot be removed (absent, ambiguous, or not removable)."""


class NotRecoverable(BrandverseError):
    """`recover` was asked for a run that has finished, never started, or is parked for a person."""


class RunLedger(Protocol):
    """How a run's status is written: the executor's functions, behind a seam a test can fake."""

    async def status(self, run_id: UUID) -> str | None: ...

    async def suspend(self, run_id: UUID, facts: RunFacts | None = None) -> None: ...

    async def resumed(self, run_id: UUID) -> None: ...

    async def end(
        self,
        run_id: UUID,
        status: Literal["completed", "partial", "blocked"],
        *,
        budget_exhausted: bool,
        memory: StagedMemory | None,
        facts: RunFacts | None,
    ) -> None: ...

    async def fail(
        self,
        run_id: UUID,
        corrections: StagedMemory | None,
        facts: RunFacts | None,
        failure: dict[str, Any] | None = None,
    ) -> None: ...


class PoolRunLedger:
    def __init__(self, pool: AsyncConnectionPool[AsyncConnection]) -> None:
        self._pool = pool

    async def status(self, run_id: UUID) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("select status from public.runs where id = %s", (run_id,))
            row = await cur.fetchone()
        return None if row is None else str(row[0])

    async def suspend(self, run_id: UUID, facts: RunFacts | None = None) -> None:
        await suspend_run(self._pool, run_id, facts)

    async def resumed(self, run_id: UUID) -> None:
        await resume_run(self._pool, run_id)

    async def end(
        self,
        run_id: UUID,
        status: Literal["completed", "partial", "blocked"],
        *,
        budget_exhausted: bool,
        memory: StagedMemory | None,
        facts: RunFacts | None,
    ) -> None:
        await end_run(
            self._pool,
            run_id,
            status,
            budget_exhausted=budget_exhausted,
            memory=memory,
            facts=facts,
        )

    async def fail(
        self,
        run_id: UUID,
        corrections: StagedMemory | None,
        facts: RunFacts | None,
        failure: dict[str, Any] | None = None,
    ) -> None:
        await fail_run(self._pool, run_id, corrections, facts, failure)


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    status: Literal["suspended", "completed", "partial", "blocked", "invalidated"]
    state: dict[str, Any]
    reason: str | None = None


OnBlocked = Callable[[RunState], Awaitable[None]]
# The deterministic gates over the state as it now stands, with no model call (graph.build_regate).
Regate = Callable[[RunState], Awaitable[dict[str, Any]]]


class CampaignStore(Protocol):
    """Where the campaign is written when the run parks and when an edit-out changes it."""

    async def park(
        self,
        *,
        run_id: UUID,
        org_id: UUID,
        strategy: Mapping[str, Any] | None,
        creative: Mapping[str, Any] | None,
        validation: Mapping[str, Any] | None,
        revision_round: int,
    ) -> None: ...

    async def revise_copy(
        self, run_id: UUID, creative: Mapping[str, Any], validation: Mapping[str, Any]
    ) -> None: ...


# Parts of the copy an edit-out may shorten. The footer is the registry's required disclaimer,
# appended by code, never by the approver; the headline and body cannot be emptied.
EDITABLE_PARTS = ("headline", "body", "call_to_action")
MUST_REMAIN = ("headline", "body")
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
        campaigns: CampaignStore | None = None,
        regate: Regate | None = None,
    ) -> None:
        self._graph, self._events, self._ledger = graph, events, ledger
        self._max_revisions, self._on_blocked = max_revisions, on_blocked
        self._campaigns, self._regate = campaigns, regate

    async def start(
        self,
        *,
        run_id: UUID,
        org_id: UUID,
        user_id: UUID,
        product_id: UUID,
        brief: str | None = None,
        depth: Depth = "D1",
        depth_policy: DepthPolicy = "requested",
        audience: str | None = None,
        brief_check: dict[str, Any] | None = None,
        arm: Arm = "proposed",
    ) -> PipelineOutcome:
        await self._events.append(RunStarted(run_id=run_id))
        initial = RunState(
            run_id=str(run_id),
            org_id=str(org_id),
            user_id=str(user_id),
            product_id=str(product_id),
            brief=brief,
            depth=depth,
            depth_policy=depth_policy,
            arm=arm,
            audience=audience,
            brief_check=brief_check,
            request={
                "brief": brief,
                "depth": depth,
                "arm": arm,
                "depth_policy": depth_policy,
                "audience": audience,
            },
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
        await self._ledger.resumed(run_id)  # the decision has arrived: the wait ends here
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
        A run parked at the interrupt is waiting for a person, not for a worker: use `resume`.

        A worker can also die AFTER the last node and BEFORE the run's ending is written: the graph
        is finished, the run still `queued`. That run is ended here from its checkpointed state."""
        config = self._config(run_id, MemoryStage())
        snapshot = await self._graph.aget_state(config)
        if not snapshot.next:
            if not snapshot.values:
                raise NotRecoverable(f"run {run_id} has no checkpoint to continue")
            if await self._ledger.status(run_id) not in ("queued", "awaiting_approval"):
                raise NotRecoverable(f"run {run_id} has already ended")
            return await self._finalize(run_id, dict(snapshot.values))
        if tuple(snapshot.next) == (APPROVAL_NODE,):
            raise NotRecoverable(f"run {run_id} is waiting for approval: use resume")
        return await self._drive(run_id, None)

    # ── internals ─────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _config(run_id: UUID, stage: MemoryStage) -> RunnableConfig:
        # A fresh stage per invocation: it lives in this process only (nodes.py).
        return {"configurable": {"thread_id": str(run_id), "stage": stage}}

    async def _saved_facts(self, config: RunnableConfig) -> RunFacts | None:
        """The run's facts from its last checkpoint, for an ending the graph did not return from."""
        try:
            return facts_from_state((await self._graph.aget_state(config)).values)
        except Exception:
            log.exception("the run's facts could not be read from its checkpoint")
            return None

    async def _drive(self, run_id: UUID, graph_input: RunState | None) -> PipelineOutcome:
        stage = MemoryStage()
        config = self._config(run_id, stage)
        try:
            values = cast(dict[str, Any], await self._graph.ainvoke(graph_input, config))
            snapshot = await self._graph.aget_state(config)
            if values.get("invalidated"):
                return await self._invalidated(run_id, values)
            if snapshot.next:
                return await self._park(run_id, values)
            if values.get("blocked"):
                return await self._blocked(run_id, values)
            await self._ledger.end(
                run_id,
                "completed",
                budget_exhausted=False,
                memory=stage.all(),
                facts=facts_from_state(values),
            )
            await self._events.append(RunCompleted(run_id=run_id))
            progress.forget(run_id)
            return PipelineOutcome("completed", values)
        except BudgetExceeded as e:
            await self._ledger.end(
                run_id,
                "partial",
                budget_exhausted=True,
                memory=stage.corrections_only(),
                facts=await self._saved_facts(config),
            )
            await self._events.append(RunCompleted(run_id=run_id, partial=True))
            return PipelineOutcome("partial", {}, str(e))
        except asyncio.CancelledError:
            raise  # nothing is written: the checkpoint is the record, and the run stays resumable
        except BaseException as e:
            infrastructure = classify_infrastructure_failure(e)
            corrections = stage.corrections_only() if infrastructure is None else None
            log.warning("run failed", extra={"infrastructure_failure": infrastructure})
            last = progress.last_started(run_id)
            failure: dict[str, Any] = {
                "kind": (
                    "step_timeout"
                    if isinstance(e, StepTimeout)
                    else "circuit_breaker"
                    if isinstance(e, ProviderCircuitOpen)
                    else "exception"
                ),
                "step": last[0] if last else None,
                "step_name": last[1] if last else None,
                "exception": type(e).__name__,
                "message": str(e)[:300],
                "infrastructure_failure": infrastructure,
            }
            await self._ledger.fail(run_id, corrections, await self._saved_facts(config), failure)
            progress.forget(run_id)
            try:
                await self._events.append(
                    RunFailed(run_id=run_id, error=f"internal error: {type(e).__name__}")
                )
            except Exception:
                log.exception("the failure could not be reported on the stream")
            raise

    async def fail_timed_out(self, run_id: UUID, after_s: float) -> dict[str, Any]:
        """End a run `failed` because its job ran out of time, and say so (migration 0046).

        An ARQ timeout used to leave the run `queued` with no error anywhere, indistinguishable
        from a run that had not started (stage-2 interim, 2026-11-04). The ending names the step
        the run last started, the time it had, and its infrastructure kind: a job timeout is
        `process_crash` in the attempt rule fixed on 2026-10-17 (core/infrastructure.py), so no
        corrections are committed, as for any infrastructure failure. Returns what was recorded.
        """
        last = progress.last_started(run_id)
        step, name = last if last is not None else (None, None)
        where = f"step {step} {name}" if last is not None else "an unknown step"
        message = f"job exceeded its {after_s:.0f} s limit while in {where}"
        failure: dict[str, Any] = {
            "kind": "job_timeout",
            "step": step,
            "step_name": name,
            "after_s": round(after_s),
            "infrastructure_failure": "process_crash",
            "message": message,
        }
        log.error("run failed: %s", message, extra={"failure": failure})
        facts = await self._saved_facts(self._config(run_id, MemoryStage()))
        await self._ledger.fail(run_id, None, facts, failure)
        try:
            await self._events.append(RunFailed(run_id=run_id, step=step, error=message))
        except Exception:
            log.exception("the timeout could not be reported on the stream")
        progress.forget(run_id)
        return failure

    async def _invalidated(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        """End a run whose agent gap is the provider's outage (Amendment 1, 2026-11-05).

        Decided by the classifier from the gap's kind alone, automatically, BEFORE the run reaches
        the approval step: no human sees the output and then decides. The run ends `failed` with
        `runs.failure.invalidated = true` and the fifth kind; no memory commits (an infrastructure
        failure discards its stage); the attempt stays in the table and is re-run by the harness
        (app/evaluation/reruns.py: at most 2 re-runs, a third failure goes to the owner)."""
        marker = dict(values["invalidated"])
        gaps = marker["gaps"]
        who = ", ".join(f"{g['agent']} (step {g['step']})" for g in gaps)
        message = f"invalidated: the provider was unavailable for {who}"
        failure = {
            **marker,
            "invalidated": True,
            "step": min(int(g["step"]) for g in gaps),
            "message": message,
        }
        log.error("run invalidated by a provider outage", extra={"failure": failure})
        await self._ledger.fail(run_id, None, facts_from_state(values), failure)
        try:
            await self._events.append(RunFailed(run_id=run_id, step=failure["step"], error=message))
        except Exception:
            log.exception("the invalidation could not be reported on the stream")
        progress.forget(run_id)
        return PipelineOutcome("invalidated", values, message)

    async def _finalize(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        """End a run whose graph finished in a process that died before the ending was written.
        What that process staged for memory went with it, so nothing is committed: an addition lost
        this way adds no trust, which is the safe direction (a correction would be lost too; none
        is staged yet, and the day one is, it must travel in the checkpointed state: nodes.py).
        The block audit is NOT written again: it may already exist, and a duplicate cannot be
        removed."""
        reason = "finished by recover: the dead process's staged memory was lost"
        log.warning("run finalized after a crash", extra={"reason": reason})
        facts = facts_from_state(values)
        if values.get("blocked"):
            log.warning("run block not audited again: the dead process may already have written it")
            message = "blocked: " + "; ".join(r["code"] for r in values["blocked"])
            await self._ledger.end(
                run_id, "blocked", budget_exhausted=False, memory=None, facts=facts
            )
            await self._events.append(RunFailed(run_id=run_id, step=TRUTH_STEP, error=message))
            return PipelineOutcome("blocked", values, message)
        await self._ledger.end(
            run_id, "completed", budget_exhausted=False, memory=None, facts=facts
        )
        await self._events.append(RunCompleted(run_id=run_id))
        return PipelineOutcome("completed", values, reason)

    async def edit_out(self, run_id: UUID, statement: str) -> dict[str, Any]:
        """The reviewer removes words from the copy of a parked campaign; the gates run again.

        The approver does not rewrite text (owner, 2026-10-29: a human rewriting copy would make
        the arm comparison measure the human): the words come out, nothing goes in. Statements the
        classifier extracted that are no longer in the copy are dropped with them, and the
        deterministic gates re-judge what is left. No model call. A7's advisory stays as stored."""
        config = self._config(run_id, MemoryStage())
        snapshot = await self._graph.aget_state(config)
        if tuple(snapshot.next) != (APPROVAL_NODE,):
            raise NotAwaitingApproval(f"run {run_id} is not waiting for approval")
        values = dict(snapshot.values)
        creative, validation = values.get("creative"), values.get("validation")
        if creative is None or validation is None or self._regate is None:
            raise EditOutRefused("there is no copy to edit, or the gates cannot be re-run")
        copy = dict(creative["copy"])
        parts = [k for k, v in copy.items() if statement in v]
        if len(parts) != 1 or copy[parts[0]].count(statement) != 1:
            raise EditOutRefused("the words must appear exactly once in the copy")
        part = parts[0]
        if part not in EDITABLE_PARTS:
            raise EditOutRefused(f"the {part} cannot be edited out")
        shortened = " ".join(copy[part].replace(statement, "", 1).split())
        if not shortened and part in MUST_REMAIN:
            raise EditOutRefused(f"the {part} cannot be emptied: reject or ask for a revision")
        copy[part] = shortened
        kept = [
            s
            for s in (validation.get("statements") or [])
            if any(s["text"] in v for v in copy.values())
        ]
        new_creative = {**creative, "copy": copy}
        regated = await self._regate(
            cast(RunState, {**values, "creative": new_creative, "validation": {"statements": kept}})
        )
        new_validation = {
            **regated,
            "statements": kept if validation.get("statements") is not None else None,
            "advisory": validation.get("advisory"),
            "edited_out": [*(validation.get("edited_out") or []), statement],
        }
        await self._graph.aupdate_state(
            config,
            {"creative": new_creative, "validation": new_validation},
            as_node="validate_evidence",
        )
        if tuple((await self._graph.aget_state(config)).next) != (APPROVAL_NODE,):
            raise EditOutRefused("the edit-out moved the run off the approval step")
        if self._campaigns is not None:
            await self._campaigns.revise_copy(run_id, new_creative, new_validation)
        return new_validation

    async def _park(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        if self._campaigns is not None:
            await self._campaigns.park(
                run_id=run_id,
                org_id=UUID(values["org_id"]),
                strategy=values.get("strategy"),
                creative=values.get("creative"),
                validation=values.get("validation"),
                revision_round=values.get("revision_round", 0),
            )
        await self._events.append(
            StepEvent(
                run_id=run_id,
                step=APPROVAL_STEP,
                name=PIPELINE_STEPS[APPROVAL_STEP - 1].name,
                status=StepStatus.STARTED,
                detail="awaiting approval",
            )
        )
        # The run's facts are written NOW, not at its end (migration 0048): an approver may take
        # days, and until then they would exist only in the checkpoint.
        await self._ledger.suspend(run_id, facts_from_state(values))
        return PipelineOutcome("suspended", values)

    async def _blocked(self, run_id: UUID, values: dict[str, Any]) -> PipelineOutcome:
        reasons = values["blocked"]
        if self._on_blocked is not None:
            try:
                await self._on_blocked(cast(RunState, values))
            except Exception:
                # The run stays blocked; a lost audit row is an error to see, not a reason to admit.
                log.exception("run block could not be audited")
        await self._ledger.end(
            run_id, "blocked", budget_exhausted=False, memory=None, facts=facts_from_state(values)
        )
        message = "blocked: " + "; ".join(r["code"] for r in reasons)
        await self._events.append(RunFailed(run_id=run_id, step=TRUTH_STEP, error=message))
        return PipelineOutcome("blocked", values, message)


def facts_from_state(values: Mapping[str, Any]) -> RunFacts:
    """The run record's facts, from the run's state. A failed agent is one gap however many times
    its body ran (a re-execution, a revision round): keyed by (agent, step)."""
    gaps: dict[tuple[str, int], dict[str, Any]] = {}
    for e in values.get("errors", []):
        gaps.setdefault(
            (e["agent"], e["step"]),
            {
                "agent": e["agent"],
                "step": e["step"],
                "error": e["error"],
                # Present only for a failure that is an infrastructure kind (so older gaps and
                # ordinary agent failures keep their shape).
                **({"infrastructure": e["infrastructure"]} if e.get("infrastructure") else {}),
            },
        )
    agents = values.get("selected_agents")
    research = values.get("research") or {}
    return RunFacts(
        depth=values.get("depth"),
        research_score=values.get("research_score"),
        selected_agents=tuple(agents) if agents else None,
        memory_hit_count=len(values["memory_hits"]) if "memory_hits" in values else None,
        search_count=(values.get("research") or {}).get("searches"),
        declared_gaps=tuple(gaps.values()),
        brief_check=values.get("brief_check"),
        timings=dict(values["timings"]) if values.get("timings") else None,
        pages_fetched=research.get("pages_fetched"),
        pages_served=research.get("pages_served"),
        facts=_checkpoint_only_facts(values),
    )


# State the analysis can need that no other row holds (decision-log 2.52, the audit of what lived
# only in the LangGraph checkpoint). Plain data only, as the checkpoint serializer requires.
_MEMORY_KEYS = {
    "memory_coverage": "coverage",
    "memory_slots": "slots",
    "memory_coverage_final": "coverage_final",
    "memory_persona_scored": "persona_scored",
    "memory_similarities": "similarities",
}


def _checkpoint_only_facts(values: Mapping[str, Any]) -> dict[str, Any] | None:
    facts: dict[str, Any] = {}
    if values.get("research"):
        facts["research"] = dict(values["research"])
    memory = {out: values[key] for key, out in _MEMORY_KEYS.items() if key in values}
    if memory:
        facts["memory"] = memory
    for key in ("agent_rationale", "revision_round", "findings"):
        if values.get(key) is not None:
            facts[key] = values[key]
    return facts or None
