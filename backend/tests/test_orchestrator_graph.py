"""The LangGraph pipeline with mocked agents and no network (Phases.md Phase 6 chunk A).

What must hold: the 15 steps run in order and emit the stream's own step events; the run parks at
the approval interrupt and continues only on a decision; one failing agent leaves a declared gap; a
budget refusal ends the run partial and a gate block ends it blocked; the revision loop is bounded;
cancellation writes nothing (the checkpoint is the record); and the real gates judge the draft.
The graph is driven over an in-memory checkpointer here; resume across a killed process, on the
Postgres checkpointer, is in tests/db/test_graph_checkpoints.py.
"""

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from app.agents.base import Agent, AgentContext
from app.agents.mock import lexical_extractor, mock_agents
from app.agents.outputs import CreativeOutput
from app.core.budgeted_run import RunFacts
from app.core.gates import GateReport, Statement, load_superlatives
from app.core.sector_registry import Sector
from app.core.truth import Claim, ProductTruth
from app.errors import (
    BudgetExceeded,
    LLMRateLimited,
    ProviderCircuitOpen,
    ProviderUnavailable,
    StepTimeout,
)
from app.llm.ledger import CallContext
from app.memory.scoring import load_scoring
from app.memory.writeback import MemoryStage, StagedMemory
from app.models.runs import RunCompleted, RunEvent, RunFailed, RunStarted, StepEvent
from app.orchestrator.adapters import PageEvidence, driver_research
from app.orchestrator.deps import (
    GraphDeps,
    MemoryResult,
    ProductContext,
    ResearchResult,
    no_memory,
)
from app.orchestrator.graph import build_graph
from app.orchestrator.nodes import PipelineInputError
from app.orchestrator.runner import (
    NotAwaitingApproval,
    NotRecoverable,
    Pipeline,
    PipelineOutcome,
    facts_from_state,
)
from app.orchestrator.state import RunState
from app.research.planner import PlanRequest, plan_queries
from app.research.search import OfflineSearch, SearchResult
from tests.snapshot_support import CFG, EXTRACTOR, GOOD, LivePages, LiveSearch, plan_for, urls_for

pytestmark = pytest.mark.anyio

ON = date(2026, 10, 29)
ORG, PRODUCT, BRAND, USER, APPROVER = (uuid4() for _ in range(5))
VERIFIED = Claim("c1", "Battery lasts 8 hours", "verified", "https://example.com/spec", False)
RESTRICTED = Claim("r1", "cures anxiety", "restricted", None, False)


class Sink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"

    def steps(self) -> list[tuple[int, str]]:
        return [(e.step, e.status.value) for e in self.events if isinstance(e, StepEvent)]


class Ledger:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.facts: list[RunFacts | None] = []
        self.failures: list[dict[str, Any] | None] = []
        self.current = "queued"

    async def status(self, run_id: UUID) -> str | None:
        return self.current

    async def suspend(self, run_id: UUID, facts: RunFacts | None = None) -> None:
        self.calls.append(("suspend",))

    async def resumed(self, run_id: UUID) -> None:
        self.calls.append(("resumed",))

    async def end(
        self,
        run_id: UUID,
        status: str,
        *,
        budget_exhausted: bool,
        memory: StagedMemory | None,
        facts: RunFacts | None,
    ) -> None:
        self.calls.append(("end", status, budget_exhausted, memory))
        self.facts.append(facts)
        self.current = status

    async def fail(
        self,
        run_id: UUID,
        corrections: StagedMemory | None,
        facts: RunFacts | None,
        failure: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(("fail", corrections))
        self.facts.append(facts)
        self.failures.append(failure)
        self.current = "failed"


def truth_with(*claims: Claim, sector: Sector | None = None) -> ProductTruth:
    return ProductTruth(sector or Sector("consumer_electronics", False), tuple(claims), ())


class Harness:
    def __init__(
        self,
        *,
        truth: ProductTruth | None = None,
        agents: Mapping[str, Agent] | None = None,
        max_revisions: int = 2,
        extractor: Any = None,
    ) -> None:
        self.sink, self.ledger = Sink(), Ledger()
        self.memory_calls: list[dict[str, Any]] = []
        self.blocked_seen: list[RunState] = []
        self.truth = truth or truth_with(VERIFIED)
        self.max_revisions = max_revisions

        async def load_context(org_id: UUID, product_id: UUID) -> ProductContext | None:
            product = {"id": str(PRODUCT), "brand_id": str(BRAND), "name": "p", "category": "x"}
            return ProductContext(product, self.truth)

        async def research(state: RunState, depth: Any) -> ResearchResult:
            return ResearchResult(
                [{"url": "https://example.com/a", "title": "t", "score": 0.7}], {"depth": depth}
            )

        async def write_memory(
            state: RunState, stage: MemoryStage, report: GateReport, truth: ProductTruth
        ) -> dict[str, Any]:
            decision = (state.get("approval") or {}).get("decision")
            self.memory_calls.append({"decision": decision, "report": report.outcome})
            return {"approved": decision == "approve"}

        self.deps = GraphDeps(
            events=self.sink,
            load_context=load_context,
            retrieve=no_memory,
            research=research,
            agents=agents or mock_agents(),
            write_memory=write_memory,
            extract_statements=extractor or lexical_extractor,
            lexicon=load_superlatives("v1"),
            today=lambda: ON,
            max_revisions=max_revisions,
        )

        async def on_blocked(state: RunState) -> None:
            self.blocked_seen.append(state)

        self.pipeline = Pipeline(
            build_graph(self.deps, InMemorySaver()),
            self.sink,
            self.ledger,
            max_revisions=max_revisions,
            on_blocked=on_blocked,
        )
        self.run_id = uuid4()

    async def start(
        self,
        *,
        brief: str | None = None,
        depth: str = "D1",
        depth_policy: str = "requested",
        brief_check: dict[str, Any] | None = None,
        arm: str = "proposed",
    ) -> PipelineOutcome:
        return await self.pipeline.start(
            run_id=self.run_id,
            org_id=ORG,
            user_id=USER,
            product_id=PRODUCT,
            brief=brief,
            depth=depth,  # type: ignore[arg-type]
            depth_policy=depth_policy,  # type: ignore[arg-type]
            arm=arm,  # type: ignore[arg-type]
            brief_check=brief_check,
        )

    async def decide(self, decision: str, reason: str | None = None) -> PipelineOutcome:
        return await self.pipeline.resume(
            self.run_id,
            decision,  # type: ignore[arg-type]
            by=APPROVER,
            reason=reason,
        )


# ── The happy path, step by step ─────────────────────────────────────────────────────────────


async def test_runs_to_the_approval_interrupt_emitting_the_streams_own_step_events() -> None:
    h = Harness()
    out = await h.start()
    assert out.status == "suspended"
    assert h.ledger.calls == [("suspend",)]
    assert isinstance(h.sink.events[0], RunStarted)
    pairs = h.sink.steps()
    for step in range(1, 12):
        assert (step, "started") in pairs and (step, "completed") in pairs, step
    assert pairs[-1] == (12, "started")  # parked here: not completed, the stream stays open
    assert (12, "completed") not in pairs and (13, "started") not in pairs
    last = h.sink.events[-1]
    assert isinstance(last, StepEvent) and last.detail == "awaiting approval"
    assert not any(isinstance(e, RunCompleted | RunFailed) for e in h.sink.events)
    assert [s for s, status in pairs if status == "started"][:5] == [1, 2, 3, 4, 5]


async def test_the_state_carries_what_each_step_produced() -> None:
    h = Harness()
    out = await h.start()
    s = out.state
    assert sorted(s["agent_outputs"]) == ["A1", "A2", "A3"]
    assert s["personas"] and s["strategy"]["claims"] == ["c1"]
    assert s["creative"]["copy"]["headline"].startswith("Battery lasts 8 hours")
    assert s["validation"]["outcome"] == "pass" and s["validation"]["findings"] == []
    assert s["findings"] == [{"url": "https://example.com/a", "title": "t", "score": 0.7}]
    assert s["memory_coverage"] == 0.0 and s["depth"] == "D1"


async def test_an_approval_completes_the_run_and_stages_memory_under_the_executors_ending() -> None:
    h = Harness()
    await h.start()
    out = await h.decide("approve")
    assert out.status == "completed"
    assert out.state["campaign"]["status"] == "approved" and out.state["campaign"]["gaps"] == []
    assert h.memory_calls == [{"decision": "approve", "report": "pass"}]
    kind, status, exhausted, memory = h.ledger.calls[-1]
    assert (kind, status, exhausted) == ("end", "completed", False)
    assert isinstance(memory, StagedMemory)
    pairs = h.sink.steps()
    assert [(n, "completed") for n in (12, 13, 14, 15)] == [
        p for p in pairs if p[0] >= 12 and p[1] == "completed"
    ]
    assert isinstance(h.sink.events[-1], RunCompleted) and not h.sink.events[-1].partial


async def test_a_rejection_is_a_completed_run_with_a_rejected_campaign_and_needs_a_reason() -> None:
    h = Harness()
    await h.start()
    with pytest.raises(ValueError, match="reason is required"):
        await h.decide("reject")
    out = await h.decide("reject", "copy is off-brand")
    assert out.status == "completed" and out.state["campaign"]["status"] == "rejected"
    assert h.memory_calls == [{"decision": "reject", "report": "pass"}]
    assert h.ledger.calls[-1][:2] == ("end", "completed")


async def test_d0_runs_the_one_fresh_query_and_skips_only_the_market_agent() -> None:
    """Decision-log 2.36: D0 is "one fresh query", so step 6 RUNS at D0 (it is not skipped), the
    research is asked for depth D0 (the planner takes one query for it), and only A2 is left out."""
    h = Harness()
    out = await h.start(depth="D0")
    assert (6, "started") in h.sink.steps() and (6, "completed") in h.sink.steps()
    assert (6, "skipped") not in h.sink.steps()
    assert out.state["research"] == {"depth": "D0"}
    assert sorted(out.state["agent_outputs"]) == ["A1", "A3"]


def _engine(h: Harness, similarities: list[float], novelty: float) -> None:
    """Wire the necessity engine into a harness whose recall returns these similarities."""

    async def retrieve(state: RunState, personas: Any = None) -> MemoryResult:
        return MemoryResult([], 0.0, 0, 2, similarities)

    async def n(state: RunState) -> float:
        return novelty

    h.deps.retrieve, h.deps.necessity, h.deps.novelty = retrieve, load_scoring().necessity, n


def warm_research_depth(out: PipelineOutcome) -> Any:
    return out.state["research"]["depth"]


async def test_the_engine_sets_the_depth_and_a2_runs_iff_the_depth_is_not_d0() -> None:
    """Phases.md Phase 7: the engine chooses the depth; A2 follows the CHOSEN depth, not the
    requested one, and the rule and its reason are kept with the run."""
    cold = Harness()
    _engine(cold, [], 1.0)
    out = await cold.start(depth="D0", depth_policy="necessity")  # asked for D0, R says D2
    assert out.state["depth"] == "D2" and out.state["necessity"]["requested"] == "D0"
    assert "A2" in out.state["selected_agents"] and (6, "started") in cold.sink.steps()
    assert out.state["agent_rationale"]["A2"].startswith("runs: depth D2")
    # the engine's record travels with the strategy, so the memory-vs-novelty decomposition can be
    # read from the campaign (decision-log 2.45)
    assert out.state["strategy"]["necessity"] == out.state["necessity"]

    warm = Harness()
    _engine(warm, [0.9] * 12, 0.02)
    out = await warm.start(depth="D2", depth_policy="necessity")  # asked for D2, R says D0
    assert out.state["depth"] == "D0" and out.state["research_score"] == out.state["necessity"]["r"]
    assert (6, "started") in warm.sink.steps()  # even a D0 run does its one fresh query
    assert warm_research_depth(out) == "D0"
    assert sorted(out.state["agent_outputs"]) == ["A1", "A3"]
    assert "A2" not in out.state["selected_agents"]
    assert out.state["agent_rationale"]["A2"].startswith("skipped: depth D0")


async def test_a_requested_depth_still_wins_under_the_requested_policy_and_r_is_advice() -> None:
    h = Harness()
    _engine(h, [0.9] * 12, 0.02)
    out = await h.start(depth="D2")
    assert out.state["depth"] == "D2" and out.state["necessity"]["advised"] == "D0"


async def test_asking_for_the_engine_on_a_pipeline_without_one_fails_the_run() -> None:
    h = Harness()
    with pytest.raises(PipelineInputError, match="no necessity engine"):
        await h.start(depth_policy="necessity")


async def test_a_research_step_over_its_timeout_fails_naming_the_step() -> None:
    """Stage-2 interim, 2026-11-04: step 6 went silent for 15 minutes and the caller's blanket wait
    named nothing. With a timeout set, the step fails with its own name and depth."""
    h = Harness()

    async def never(state: RunState, depth: Any) -> ResearchResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    h.deps.research = never
    h.deps.research_timeout_s = 0.05
    with pytest.raises(StepTimeout, match=r"step 6 research \(D2\) exceeded 0 s"):
        await h.start(depth="D2")
    assert (6, "failed") in h.sink.steps()
    assert h.ledger.calls[0][0] == "fail"
    failure = h.ledger.failures[0]
    assert failure is not None and failure["kind"] == "step_timeout"
    assert (failure["step"], failure["step_name"]) == (6, "research")


async def test_with_no_timeout_set_research_is_not_cut_short() -> None:
    h = Harness()
    assert h.deps.research_timeout_s is None
    out = await h.start(depth="D2")
    assert (6, "completed") in h.sink.steps() and out.state["research"]["depth"] == "D2"


async def test_every_step_logs_a_named_start_and_end(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness()
    with caplog.at_level("INFO", logger="app.orchestrator.nodes"):
        await h.start(depth="D1")
    lines = [r.getMessage() for r in caplog.records]
    assert "step 6 research: start" in lines
    assert any(line.startswith("step 6 research: done in ") for line in lines)
    assert any(line.startswith("step 1 normalise_request: start") for line in lines)


# ── Failure, budget and blocking ─────────────────────────────────────────────────────────────


class Failing:
    def __init__(self, agent_id: str, error: Exception) -> None:
        self.id, self.error = agent_id, error

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
        raise self.error


async def test_a_failing_agent_leaves_a_declared_gap_and_the_run_continues() -> None:
    agents = {**mock_agents(), "A2": Failing("A2", RuntimeError("engine down"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "suspended"
    assert [e["agent"] for e in out.state["errors"]] == ["A2"]
    assert out.state["strategy"]["gaps"] == ["A2"]
    done = await h.decide("approve")
    assert done.status == "completed"
    assert done.state["campaign"]["gaps"] == [{"agent": "A2", "step": 7, "error": "RuntimeError"}]
    assert (7, "completed") in h.sink.steps()


async def test_a_failing_creative_agent_still_reaches_the_approver_with_a_block() -> None:
    agents = {**mock_agents(), "A6": Failing("A6", RuntimeError("no model"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "suspended" and out.state["creative"] is None
    assert out.state["validation"]["findings"][0]["kind"] == "no_creative"


async def test_a_budget_refusal_ends_the_run_partial_with_corrections_only() -> None:
    agents = {**mock_agents(), "A1": Failing("A1", BudgetExceeded("run call cap"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "partial" and out.reason == "run call cap"
    kind, status, exhausted, memory = h.ledger.calls[-1]
    assert (kind, status, exhausted) == ("end", "partial", True)
    assert isinstance(memory, StagedMemory) and not memory.adds_trust
    assert not any(c[0] == "suspend" for c in h.ledger.calls)
    # The stream can tell a partial run from a complete one (chunk E).
    last = h.sink.events[-1]
    assert isinstance(last, RunCompleted) and last.partial is True


async def test_an_open_provider_breaker_ends_the_run_failed_as_infrastructure() -> None:
    """Tonight's outage (5 November): three consecutive 503s open the run's breaker. The run is
    ABORTED, not turned into declared gaps, and the ending carries the fifth kind."""
    agents = {**mock_agents(), "A1": Failing("A1", ProviderCircuitOpen("3 consecutive 503"))}
    h = Harness(agents=agents)
    with pytest.raises(ProviderCircuitOpen):
        await h.start()
    assert h.ledger.calls[-1][0] == "fail"
    failure = h.ledger.failures[0]
    assert failure is not None
    assert failure["kind"] == "circuit_breaker"
    assert failure["infrastructure_failure"] == "provider_unavailable"
    assert isinstance(h.sink.events[-1], RunFailed)
    assert h.ledger.calls[-1][1] is None  # infrastructure failure: no corrections commit


async def test_a_provider_unavailable_gap_invalidates_the_run_before_any_human_sees_it() -> None:
    """Owner ruling, 5 November 2026. One call ran out of retries while others succeeded in
    between, so the breaker never opened and the run would have ended `completed` with a gap. The
    classifier invalidates it AUTOMATICALLY, from the gap's kind alone, before the approval step:
    nothing is parked for a person, no memory is written, and the copy is never produced."""
    agents = {**mock_agents(), "A1": Failing("A1", ProviderUnavailable("503 x3, others fine"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "invalidated"
    assert not any(c[0] == "suspend" for c in h.ledger.calls)  # never parked for an approver
    assert h.ledger.calls[-1][0] == "fail" and h.ledger.calls[-1][1] is None  # no corrections
    assert h.memory_calls == []  # step 15 never ran: nothing reached memory
    assert (8, "started") not in h.sink.steps()  # no persona, strategy or copy steps were spent
    failure = h.ledger.failures[0]
    assert failure is not None and failure["invalidated"] is True
    assert failure["kind"] == "infrastructure_gap"
    assert failure["infrastructure_failure"] == "provider_unavailable"
    assert failure["gaps"] == [{"step": 7, "agent": "A1", "error": "ProviderUnavailable"}]
    last = h.sink.events[-1]
    assert isinstance(last, RunFailed) and last.step == 7 and "A1" in last.error


async def test_an_outage_gap_in_the_copy_step_also_ends_the_run_before_approval() -> None:
    agents = {**mock_agents(), "A6": Failing("A6", ProviderUnavailable("503 on the copy"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "invalidated"
    assert not any(c[0] == "suspend" for c in h.ledger.calls)
    assert h.memory_calls == []
    failure = h.ledger.failures[0]
    assert failure is not None and failure["gaps"][0]["agent"] == "A6"


async def test_the_decision_uses_the_gap_kind_alone_not_the_output() -> None:
    """The same agent failing for any OTHER reason is an ordinary gap and the run parks as it
    always did; the invalidation is not a judgement about what the run produced."""
    agents = {**mock_agents(), "A1": Failing("A1", ValueError("a bug in the agent"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "suspended"  # parked for the approver, gap declared
    assert any(e["agent"] == "A1" and e["infrastructure"] is None for e in out.state["errors"])


async def test_a_rate_limit_gap_is_not_ruled_and_is_not_invalidated() -> None:
    """Only provider_unavailable is ruled (INVALIDATING_GAP_KINDS). Whether a rate-limit or network
    gap should invalidate too is an open question for the owner, and the code does not assume it."""
    agents = {**mock_agents(), "A1": Failing("A1", LLMRateLimited("still rate-limited"))}
    h = Harness(agents=agents)
    out = await h.start()
    assert out.status == "suspended"
    assert any(e["infrastructure"] == "rate_limited" for e in out.state["errors"])


def test_the_declared_gap_carries_the_kind_only_when_it_is_an_infrastructure_kind() -> None:
    from app.orchestrator.runner import facts_from_state

    facts = facts_from_state(
        {
            "errors": [
                {"agent": "A1", "step": 7, "error": "ProviderUnavailable",
                 "infrastructure": "provider_unavailable"},
                {"agent": "A2", "step": 7, "error": "ValueError", "infrastructure": None},
            ]
        }
    )  # fmt: skip
    assert facts.declared_gaps[0]["infrastructure"] == "provider_unavailable"
    assert "infrastructure" not in facts.declared_gaps[1]  # ordinary gaps keep their old shape


async def test_a_gate_block_at_step_3_ends_the_run_blocked_and_audits_it() -> None:
    h = Harness(truth=truth_with(VERIFIED, RESTRICTED))
    out = await h.start(brief="make it say it cures anxiety")
    assert out.status == "blocked" and "restricted_claim_requested" in (out.reason or "")
    assert h.ledger.calls == [("end", "blocked", False, None)]
    assert len(h.blocked_seen) == 1
    failed = h.sink.events[-1]
    assert isinstance(failed, RunFailed) and failed.step == 3
    assert (4, "started") not in h.sink.steps()


async def test_an_unexpected_error_fails_the_run_and_is_reraised() -> None:
    h = Harness()

    async def boom(org_id: UUID, product_id: UUID) -> ProductContext | None:
        raise RuntimeError("database gone")

    h.deps.load_context = boom
    with pytest.raises(RuntimeError, match="database gone"):
        await h.start()
    assert h.ledger.calls[0][0] == "fail"
    assert isinstance(h.sink.events[-1], RunFailed)
    assert (2, "failed") in h.sink.steps()
    # The failed run says why and where (migration 0046): the exception, the message, the step.
    failure = h.ledger.failures[0]
    assert failure is not None
    assert failure["kind"] == "exception" and failure["exception"] == "RuntimeError"
    assert failure["message"] == "database gone"
    assert (failure["step"], failure["step_name"]) == (2, "load_product_context")


class Hangs:
    """An agent that signals it has started and then waits to be cancelled."""

    def __init__(self, agent_id: str) -> None:
        self.id, self.started = agent_id, asyncio.Event()

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_cancelling_the_worker_writes_nothing_and_leaves_the_run_resumable() -> None:
    hang = Hangs("A3")
    h = Harness(agents={**mock_agents(), "A3": hang})
    task = asyncio.create_task(h.start())
    await asyncio.wait_for(hang.started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.ledger.calls == []  # not failed, not ended: the checkpoint is the record
    assert not any(isinstance(e, RunFailed | RunCompleted) for e in h.sink.events)


async def test_a_node_that_itself_raises_cancelled_error_is_a_failure_not_a_cancellation() -> None:
    """LangGraph turns it into NodeCancelledError, so the run is failed and reported."""
    agents = {**mock_agents(), "A3": Failing("A3", asyncio.CancelledError())}
    h = Harness(agents=agents)
    with pytest.raises(Exception, match="raised asyncio.CancelledError"):
        await h.start()
    assert h.ledger.calls[0][0] == "fail" and isinstance(h.sink.events[-1], RunFailed)


# ── The revision loop and the interrupt ──────────────────────────────────────────────────────


class Drafts:
    """A6 that writes a draft each time it is asked, and counts."""

    id = "A6"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        self.calls += 1
        return CreativeOutput(copy={"headline": "Battery lasts 8 hours."})


class BadCites:
    """The classifier stand-in: the first `bad` extractions cite a claim that does not exist."""

    def __init__(self, bad: int) -> None:
        self.bad, self.calls = bad, 0

    async def __call__(self, ctx: AgentContext, copy: Mapping[str, str]) -> list[Statement]:
        self.calls += 1
        claim = "no-such-claim" if self.calls <= self.bad else "c1"
        return [Statement("Battery lasts 8 hours", claim)]


async def test_a_blocked_draft_is_regenerated_until_the_gates_pass() -> None:
    a6, extractor = Drafts(), BadCites(bad=1)
    h = Harness(agents={**mock_agents(), "A6": a6}, extractor=extractor)
    out = await h.start()
    assert a6.calls == 2 and extractor.calls == 2 and out.state["revision_round"] == 1
    assert out.state["validation"]["outcome"] == "pass"


async def test_regeneration_is_bounded_and_the_approver_sees_what_is_still_blocked() -> None:
    a6 = Drafts()
    h = Harness(agents={**mock_agents(), "A6": a6}, extractor=BadCites(bad=99))
    out = await h.start()
    assert out.status == "suspended" and a6.calls == 3  # the draft plus two rounds
    assert out.state["revision_round"] == 2
    assert out.state["validation"]["outcome"] == "block"
    assert [f["kind"] for f in out.state["validation"]["findings"]] == ["cites_unknown_claim"]


async def test_revise_is_a_regeneration_round_inside_the_same_bound() -> None:
    a6 = Drafts()
    h = Harness(agents={**mock_agents(), "A6": a6}, max_revisions=1, extractor=BadCites(bad=0))
    await h.start()
    with pytest.raises(ValueError, match="reason is required"):
        await h.decide("revise")
    again = await h.decide("revise", "tone is too flat")
    assert again.status == "suspended" and a6.calls == 2 and again.state["revision_round"] == 1
    assert again.state["approval"] is None  # the earlier decision does not carry over
    assert [c[0] for c in h.ledger.calls] == ["suspend", "resumed", "suspend"]
    with pytest.raises(ValueError, match="no regeneration rounds left"):
        await h.decide("revise", "again")
    assert (await h.decide("approve")).status == "completed"


async def test_a_decision_is_refused_unless_the_run_is_parked_at_the_interrupt() -> None:
    h = Harness()
    with pytest.raises(NotAwaitingApproval):
        await h.decide("approve")  # never started
    await h.start()
    await h.decide("approve")
    with pytest.raises(NotAwaitingApproval):
        await h.decide("approve")  # already finished


# ── Gates, memory and research are the real ones ─────────────────────────────────────────────


async def test_the_real_gates_judge_a_draft_against_the_truth_as_it_is_now() -> None:
    h = Harness()
    await h.start()
    h.truth = truth_with(VERIFIED, Claim("r2", "Battery lasts 8 hours", "restricted", None, False))
    out = await h.decide("approve")  # step 15 re-runs the gates on the truth at that moment
    assert out.status == "completed"
    assert h.memory_calls == [{"decision": "approve", "report": "block"}]


async def test_retrieval_without_its_engine_says_nothing_was_retrieved() -> None:
    assert await no_memory({}) == MemoryResult([], 0.0)


async def test_the_research_adapter_returns_attributed_findings_and_no_page_text() -> None:
    request = PlanRequest(
        sector="consumer electronics", regulated=False, category="neckband earphones"
    )
    plan = plan_queries(request, queries_per_facet=2, locale="India", max_pages_per_query=100)
    urls = {q.rank: f"https://r{q.rank}.example/p" for q in plan.queries}
    search = OfflineSearch(
        {
            q.text: [SearchResult(urls[q.rank], "t", "s", "yep", None, ("yep",))]
            for q in plan.queries
        },
        declared_engines=("yep",),
    )
    pages = LivePages({u: GOOD for u in urls.values()})
    run = driver_research(
        search,
        pages,
        EXTRACTOR,
        CFG.source,
        queries_per_facet=2,
        locale="India",
        max_pages_per_query=100,
        today=lambda: ON,
    )
    state = RunState(
        product={"category": "neckband earphones"},
        truth={"sector": "consumer_electronics", "regulated": False},
    )
    result = await run(state, "D2")
    assert result.report["searches"] == len(plan.queries) > 0
    assert result.findings, "an accepted page must come back as a finding"
    for f in result.findings:
        # "components" is the score broken down: write-back needs it to record the source honestly
        assert set(f) == {
            "url",
            "title",
            "score",
            "components",
            "published",
            "queries",
            "engines",
            "first_party",
        }
    assert "text" not in repr(result.findings) and GOOD[:30] not in repr(result.findings)


# ── The run record says how the run went, not only that it ended ─────────────────────────────


async def test_a_completed_run_with_a_failed_agent_is_told_from_a_whole_one_in_the_run_record() -> (
    None
):
    whole = Harness()
    await whole.start()
    await whole.decide("approve")
    assert whole.ledger.facts[-1] is not None and whole.ledger.facts[-1].declared_gaps == ()

    agents = {**mock_agents(), "A2": Failing("A2", RuntimeError("engine down"))}
    gappy = Harness(agents=agents)
    await gappy.start()
    await gappy.decide("approve")
    facts = gappy.ledger.facts[-1]
    assert gappy.ledger.calls[-1][:2] == ("end", "completed")
    assert facts is not None
    assert facts.declared_gaps == ({"agent": "A2", "step": 7, "error": "RuntimeError"},)
    assert (facts.depth, facts.selected_agents) == ("D1", ("A1", "A2", "A3", "A6", "A7"))
    # No necessity engine is wired in this harness, so no R is recorded (it used to be the
    # placeholder 1 - coverage; the engine is built, decision-log 2.35).
    assert facts.memory_hit_count == 0 and facts.research_score is None


async def test_a_partial_run_records_its_facts_from_the_checkpoint() -> None:
    agents = {**mock_agents(), "A1": Failing("A1", BudgetExceeded("run call cap"))}
    h = Harness(agents=agents)
    await h.start()
    facts = h.ledger.facts[-1]
    assert h.ledger.calls[-1][:3] == ("end", "partial", True)
    assert facts is not None and facts.depth == "D1" and facts.selected_agents is not None


def test_a_gap_is_one_however_many_times_its_agent_failed() -> None:
    error = {"step": 10, "agent": "A6", "error": "RuntimeError", "message": "x"}
    facts = facts_from_state({"errors": [error, error, {**error, "step": 11, "agent": "A7"}]})
    assert [(g["agent"], g["step"]) for g in facts.declared_gaps] == [("A6", 10), ("A7", 11)]


async def test_recover_ends_a_run_whose_graph_finished_in_a_process_that_died_before_ending() -> (
    None
):
    h = Harness()
    await h.start()
    real_end = h.ledger.end

    async def dies(*args: Any, **kwargs: Any) -> None:
        raise asyncio.CancelledError  # the worker is gone before the status is written

    h.ledger.end = dies  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await h.decide("approve")
    assert h.ledger.current == "queued" and not any(c[0] == "end" for c in h.ledger.calls)
    h.ledger.end = real_end  # type: ignore[method-assign]

    out = await h.pipeline.recover(h.run_id)
    assert out.status == "completed" and "staged memory was lost" in (out.reason or "")
    assert h.ledger.calls[-1][:2] == ("end", "completed") and h.ledger.calls[-1][3] is None
    with pytest.raises(NotRecoverable, match="already ended"):
        await h.pipeline.recover(h.run_id)


async def test_recover_refuses_a_parked_run_and_a_run_that_never_started() -> None:
    h = Harness()
    with pytest.raises(NotRecoverable, match="no checkpoint"):
        await h.pipeline.recover(h.run_id)
    await h.start()
    with pytest.raises(NotRecoverable, match="use resume"):
        await h.pipeline.recover(h.run_id)


# ── Every model call says which execution made it (migration 0035) ───────────────────────────


def test_a_call_context_sets_step_round_and_execution_together_or_not_at_all() -> None:
    CallContext(ORG, uuid4())  # a call outside the graph
    CallContext(ORG, uuid4(), "A3", 7, 0, uuid4())
    for partial in ({"step": 7}, {"execution_id": uuid4()}, {"revision_round": 0}):
        with pytest.raises(ValueError, match="set together"):
            CallContext(ORG, uuid4(), **partial)  # type: ignore[arg-type]


async def test_each_agent_execution_gets_its_own_execution_id_and_its_step_and_round() -> None:
    seen: list[CallContext] = []

    class Records:
        def __init__(self, agent_id: str) -> None:
            self.id = agent_id

        async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
            seen.append(ctx.call_context(self.id))
            return await mock_agents()[self.id].run(state, ctx)

    agents = {**mock_agents(), **{a: Records(a) for a in ("A1", "A3", "A6", "A7")}}
    h = Harness(agents=agents)
    await h.start()
    await h.decide("revise", "again")
    by_agent = {a: [c for c in seen if c.agent_id == a] for a in ("A1", "A3", "A6", "A7")}
    assert [(c.step, c.revision_round) for c in by_agent["A1"]] == [(7, 0)]
    assert [(c.step, c.revision_round) for c in by_agent["A6"]] == [(10, 0), (10, 1)]
    assert [(c.step, c.revision_round) for c in by_agent["A7"]] == [(11, 0), (11, 1)]
    ids = [c.execution_id for c in seen]
    assert len(set(ids)) == len(ids), "every execution of a body is its own execution"
    with pytest.raises(ValueError, match="need the graph step"):
        AgentContext(uuid4(), ORG, truth_with(VERIFIED), ON).call_context("A1")


# ── An agent can read a finding's text under replay ──────────────────────────────────────────


async def test_an_agent_reads_a_findings_text_from_the_snapshot_never_from_the_network(
    tmp_path,
) -> None:
    from app.research.snapshot import SnapshotMiss, SnapshotPages, SnapshotStore, record_request
    from tests.snapshot_support import DECLARED, TODAY, no_sleep

    store = SnapshotStore.create(tmp_path / "evaluation-snapshot")
    plan = plan_for()
    pages = LivePages()
    await record_request(
        "R01",
        plan,
        store=store,
        search=LiveSearch(plan, urls_for(plan)),
        pages=pages,
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=no_sleep,
    )
    url = "https://r1-0.example/p"
    reader = PageEvidence(SnapshotPages(store), EXTRACTOR)
    recorded = await reader.text(url)
    assert recorded is not None and "neckband earphones" in recorded
    pages.bodies[url] = "the live page has since changed"  # a live fetch would now differ
    assert await reader.text(url) == recorded
    with pytest.raises(SnapshotMiss):  # never answered from the network
        await reader.text("https://never-recorded.example/p")

    class Reads:
        id = "A2"

        async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
            assert ctx.evidence is not None
            text = await ctx.evidence.text(state["findings"][0]["url"])
            return CreativeOutput(copy={"headline": text or ""})

    h = Harness(agents={**mock_agents(), "A2": Reads()})
    h.deps.evidence = reader

    async def research(state: RunState, depth: Any) -> ResearchResult:
        return ResearchResult([{"url": url, "title": "t", "score": 0.7}], {})

    h.deps.research = research
    out = await h.start()
    assert out.state["agent_outputs"]["A2"]["copy"]["headline"] == recorded
    assert recorded not in repr(out.state["findings"])  # the text stayed out of the state


# ── Memory is recalled twice: before the persona exists, and again once A3 has spoken ────────


class RecordingRetrieve:
    """A retrieval port that records what it was asked, and answers with a hit whose components say
    whether the persona was supplied."""

    def __init__(self) -> None:
        self.asked: list[Any] = []

    async def __call__(self, state: RunState, personas: Any = None) -> MemoryResult:
        self.asked.append(None if personas is None else list(personas))
        left_out = ["C"] if personas is None else []
        hit = {"id": "i1", "kind": "insight", "m": 0.9, "left_out": left_out}
        return MemoryResult([hit], 0.5 if personas is None else 0.75)


async def test_memory_is_recalled_before_the_persona_and_again_with_it() -> None:
    h = Harness()
    port = RecordingRetrieve()
    h.deps.retrieve = port
    out = await h.start()
    assert port.asked == [None, ["mock persona"]]
    state = out.state
    assert state["memory_persona_scored"] is True
    assert state["memory_hits"][0]["left_out"] == []  # the hits in use are the persona-scored ones
    assert (state["memory_coverage"], state["memory_coverage_final"]) == (0.5, 0.75)
    detail = [e.detail for e in h.sink.events if isinstance(e, StepEvent) and e.step == 8][-1]
    assert detail is not None and "re-scored with C" in detail


async def test_without_personas_the_first_recall_stands_and_says_it_has_no_c() -> None:
    class NoPersonas:
        id = "A3"

        async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
            raise RuntimeError("A3 down")

    h = Harness(agents={**mock_agents(), "A3": NoPersonas()})
    port = RecordingRetrieve()
    h.deps.retrieve = port
    out = await h.start()
    assert port.asked == [None], "no persona, no second recall"
    assert out.state["memory_persona_scored"] is False
    assert out.state["memory_hits"][0]["left_out"] == ["C"]
    assert "memory_coverage_final" not in out.state
