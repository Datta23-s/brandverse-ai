"""The 15 pipeline steps as graph nodes (Architecture.md §5.1), wired to the pieces that exist.

Every step emits the same `StepEvent`s the fake run does, so the SSE endpoint and the stepper need
no change. Nodes return partial state updates; a node's `_detail` key becomes its event's detail.

**At-least-once.** A worker killed inside a node resumes from the last checkpoint and runs that node
again, so a node must be safe to repeat. The ones here only compute and emit events; the one step
with a durable effect, 15, stages into a `MemoryStage` that exists for one invocation (below).

**Nothing is staged before the interrupt.** The stage lives in the process (`budgeted_run.py`), and
the interrupt can last days and end on another worker. So the nodes before step 12 stage nothing,
and anything they observe that step 15 must act on (§6.3 corrections) belongs in the
checkpointed state; the runner hands step 15 a fresh stage. No node observes such a correction yet.

**Failure isolation.** An agent that raises is recorded in `errors` and the run goes on with a
declared gap. `BudgetExceeded` is not an agent failure and is never swallowed: the run ends partial.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send

from app.agents.base import AgentContext
from app.agents.market import PAGES_BY_DEPTH
from app.core.admission import AdmissionInputs, RestrictedClaim, decide_admission
from app.core.brief_check import assess, reconcile
from app.core.citation_terms import neutral_stems
from app.core.gates import Finding, GateReport, Statement, evaluate, finding_dict
from app.core.infrastructure import classify_infrastructure_failure, invalidating_gaps
from app.core.pipeline import PIPELINE_STEPS
from app.core.timing import collecting
from app.core.truth import ProductTruth
from app.errors import ABORTS, BrandverseError, StepTimeout
from app.memory.necessity import decide
from app.memory.writeback import MemoryStage
from app.models.runs import StepEvent, StepStatus
from app.orchestrator import progress
from app.orchestrator.deps import GraphDeps
from app.orchestrator.state import AgentTask, Depth, RunState

log = logging.getLogger(__name__)

# Step 7b. A6 runs in step 10 and A7 in step 11: they need the strategy and the draft, which do not
# exist at step 7.
FAN_OUT_AGENTS = ("A1", "A2", "A3")
ADMISSION_STEP, RESEARCH_STEP, AGENTS_STEP = 3, 6, 7
CREATIVE_STEP, VALIDATION_STEP, MEMORY_STEP = 10, 11, 15


class NodeFn(Protocol):
    """A graph node: LangGraph passes `config` only to a callable that names the parameter."""

    def __call__(self, state: RunState, config: RunnableConfig) -> Awaitable[dict[str, Any]]: ...


class PipelineInputError(BrandverseError):
    """The run cannot start: the request names a product the organisation does not have."""


def _ids(state: RunState) -> tuple[UUID, UUID, UUID]:
    return UUID(state["run_id"]), UUID(state["org_id"]), UUID(state["product_id"])


def naming_terms(state: RunState) -> frozenset[str]:
    """The stems of the words that merely name the product: its category and its name. The citation
    check (threat V12) does not count them as something a statement asserts."""
    product = state.get("product") or {}
    return neutral_stems([product.get("category"), product.get("name")])


def report_dict(report: GateReport) -> dict[str, Any]:
    return {
        "outcome": report.outcome,
        "sector": report.sector,
        "superlatives_version": report.superlatives_version,
        "checked_on": report.checked_on.isoformat(),
        "rules_applied": list(report.rules_applied),
        "findings": [finding_dict(f) for f in report.findings],
    }


def _agent_error(step: int, agent: str, e: Exception) -> dict[str, Any]:
    return {
        "step": step,
        "agent": agent,
        "error": type(e).__name__,
        "message": str(e)[:300],
        # The KIND of the failure, decided from the exception alone (core/infrastructure.py): a
        # gap of an invalidating kind ends the run before any human sees its output.
        "infrastructure": classify_infrastructure_failure(e),
    }


class Nodes:
    """The graph's nodes and routers, built over one `GraphDeps`."""

    def __init__(self, deps: GraphDeps) -> None:
        self.d = deps

    # ── events ────────────────────────────────────────────────────────────────────────────────
    async def emit(
        self, state: RunState, step: int, status: StepStatus, detail: str | None = None
    ) -> None:
        await self.d.events.append(
            StepEvent(
                run_id=UUID(state["run_id"]),
                step=step,
                name=PIPELINE_STEPS[step - 1].name,
                status=status,
                detail=detail,
            )
        )

    def stepped(
        self,
        step: int,
        fn: Callable[[RunState], Awaitable[dict[str, Any]]],
        *,
        start: bool = True,
        complete: bool = True,
    ) -> NodeFn:
        """Wrap a node body so its step emits started / completed / failed."""

        name = PIPELINE_STEPS[step - 1].name

        async def node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
            # A named line at each end of every step, so silence localises to a step in the worker
            # log and not to a blank (stage-2 interim, 2026-11-04). `emit` also writes the stream.
            began = time.perf_counter()
            progress.started(UUID(state["run_id"]), step, name)
            log.info("step %d %s: start", step, name, extra={"step": step, "step_name": name})
            if start:
                await self.emit(state, step, StepStatus.STARTED)
            try:
                update = await fn(state)
            except Exception as e:  # cancellation passes through: the runner reports that
                log.warning(
                    "step %d %s: FAILED after %.1f s: %s: %s",
                    step,
                    name,
                    time.perf_counter() - began,
                    type(e).__name__,
                    str(e)[:200],
                    extra={"step": step, "step_name": name},
                )
                await self.emit(state, step, StepStatus.FAILED, f"{type(e).__name__}: {e}"[:300])
                raise
            log.info(
                "step %d %s: done in %.1f s",
                step,
                name,
                time.perf_counter() - began,
                extra={"step": step, "step_name": name},
            )
            detail = update.pop("_detail", None)
            if complete:
                await self.emit(state, step, StepStatus.COMPLETED, detail)
            return update

        return node

    # ── context ───────────────────────────────────────────────────────────────────────────────
    async def context(
        self, state: RunState, step: int = 0, revision_round: int = 0
    ) -> AgentContext:
        """The truth as it is now, and, for a body that makes agent calls (`step` > 0), a NEW
        execution id: each time a body starts it is a different execution, so a body re-run from a
        checkpoint after its worker died is told apart from the first (migration 0035)."""
        run_id, org_id, product_id = _ids(state)
        loaded = await self.d.load_context(org_id, product_id)
        if loaded is None:
            raise PipelineInputError(f"no product {product_id} in organisation {org_id}")
        return AgentContext(
            run_id,
            org_id,
            loaded.truth,
            self.d.today(),
            step=step,
            revision_round=revision_round,
            execution_id=uuid4() if step else None,
            evidence=self.d.evidence,
        )

    # ── steps 1 to 5 ──────────────────────────────────────────────────────────────────────────
    async def normalise_request(self, state: RunState) -> dict[str, Any]:
        brief = (state.get("brief") or "").strip() or None
        return {
            "brief": brief,
            "depth": state.get("depth", "D1"),
            "revision_round": 0,
            "budget": state.get("budget", {}),
        }

    async def load_product_context(self, state: RunState) -> dict[str, Any]:
        _, org_id, product_id = _ids(state)
        loaded = await self.d.load_context(org_id, product_id)
        if loaded is None:
            raise PipelineInputError(f"no product {product_id} in organisation {org_id}")
        truth = loaded.truth
        return {
            "product": loaded.product,
            "truth": {
                "sector": truth.sector.id if truth.sector else None,
                "regulated": bool(truth.sector and truth.sector.regulated),
                "verified": len(truth.active("verified")),
                "deduced": len(truth.active("deduced")),
                "restricted": len(truth.active("restricted")),
                "brand_domains": list(truth.brand_domains),
            },
        }

    async def validate_product_truth(self, state: RunState) -> dict[str, Any]:
        ctx = await self.context(state, ADMISSION_STEP)
        truth = ctx.truth
        inputs = AdmissionInputs(
            truth.sector,
            len(truth.active("verified")),
            tuple(RestrictedClaim(c.id, c.text) for c in truth.active("restricted")),
            truth.rules,
        )
        decision = decide_admission(inputs, state.get("brief"), ctx.on)
        if not decision.blocked:
            check = await self.brief_check(state, ctx)
            n = len(check["unbacked"])
            detail = f"brief: {check['status']}" + (
                f", {n} property assertion(s) no claim backs" if n else ""
            )
            return {"blocked": None, "brief_check": check, "_detail": detail}
        return {
            "blocked": [
                {"code": r.code, "message": r.message, "refs": list(r.refs)}
                for r in decision.reasons
            ],
            "_detail": "blocked: " + "; ".join(r.code for r in decision.reasons),
        }

    async def brief_check(self, state: RunState, ctx: AgentContext) -> dict[str, Any]:
        """The run's brief check: the request's PRECOMPUTED record when it carries one (the
        evaluation: every arm of a request reads the same record, app/core/brief_check.py), else
        computed here. A precomputed record is never recomputed, even when stale."""
        brief = state.get("brief")
        record = state.get("brief_check")
        if record is not None and brief:
            return reconcile(record, brief, ctx.truth)
        return await self.check_brief(state, ctx)

    async def check_brief(self, state: RunState, ctx: AgentContext) -> dict[str, Any]:
        """FLAG AND RECORD (owner, 2026-10-30), never block: which property assertions in the
        brief no listed claim backs. Blocking would refuse every request whose brief mentions an
        attribute the claims lack, and how many of the evaluation's requests that is has not been
        counted. A failed check is recorded as unavailable and the run goes on; a budget refusal
        still ends it."""
        brief = state.get("brief")
        if not brief or self.d.check_brief is None:
            return {"status": "skipped", "unbacked": [], "backed": 0}
        try:
            statements = await self.d.check_brief(ctx, {"brief": brief})
        except ABORTS:
            raise
        except Exception as e:
            log.warning("the brief check failed; recorded as unavailable", exc_info=True)
            return {"status": "unavailable", "unbacked": [], "backed": 0, "error": type(e).__name__}
        return assess(brief, ctx.truth, statements)

    def route_after_truth(self, state: RunState) -> str:
        return "blocked" if state.get("blocked") else "ok"

    async def retrieve_memory(self, state: RunState) -> dict[str, Any]:
        # Before A3: the request's persona does not exist yet, so C is left out of every M here
        # (app/memory/recall.py). This coverage is what the research-necessity step reads.
        with collecting() as timings:
            found = await self.d.retrieve(state, None)
        return {
            "timings": timings,
            "memory_hits": found.hits,
            "memory_coverage": found.coverage,
            "memory_slots": {"filled": found.slots_filled, "of": found.slots_total},
            "memory_similarities": list(found.similarities),
            "memory_persona_scored": False,
            "_detail": f"{len(found.hits)} hits, coverage {found.slots_filled} of "
            f"{found.slots_total} slots ({found.coverage:.2f}; no persona yet)",
        }

    async def assess_research_necessity(self, state: RunState) -> dict[str, Any]:
        """Step 5, the Research Necessity Engine (app/memory/necessity.py, frozen parameters).

        Under the "necessity" policy R chooses the depth; under "requested" the run keeps the depth
        the request named, and R is still computed and recorded as what the engine ADVISED, so the
        record shows what it would have done. A run that asks for the engine on a pipeline that has
        none FAILS: falling back to the requested depth would be a run that looks adaptive and is
        not (the same reasoning as the call caps: no silent exemption)."""
        requested: Depth = state.get("depth", "D1")
        policy = state.get("depth_policy", "requested")
        cfg = self.d.necessity
        if cfg is None:
            if policy == "necessity":
                raise PipelineInputError(
                    "depth_policy is 'necessity' but this pipeline has no necessity engine wired"
                )
            return {
                "depth": requested,
                "_detail": f"depth {requested} (as requested; no necessity engine wired)",
            }
        if self.d.novelty is None and policy == "necessity":
            raise PipelineInputError("depth_policy is 'necessity' but N has no source wired")
        novelty = 1.0 if self.d.novelty is None else await self.d.novelty(state)
        decision = decide(
            cfg,
            novelty=novelty,
            similarities=state.get("memory_similarities", []),
            regulated=bool((state.get("truth") or {}).get("regulated")),
            requested=requested,
            policy=policy,
        )
        how = (
            "chosen by R"
            if policy == "necessity"
            else f"as requested; R advised {decision.advised}"
        )
        detail = (
            f"depth {decision.depth} ({how}): R {decision.r:.3f}, N {decision.novelty:.3f}, "
            f"U {decision.u:.2f} ({decision.n_relevant} relevant of {decision.n_recalled} recalled)"
        )
        return {
            "depth": decision.depth,
            "research_score": round(decision.r, 4),
            "necessity": decision.record(),
            "_detail": detail,
        }

    def route_depth(self, state: RunState) -> str:
        return state.get("depth", "D1")

    # ── step 6 ────────────────────────────────────────────────────────────────────────────────
    def research_node(self, depth: Depth) -> NodeFn:
        async def body(state: RunState) -> dict[str, Any]:
            started = time.perf_counter()
            with collecting() as timings:
                try:
                    result = await asyncio.wait_for(
                        self.d.research(state, depth), timeout=self.d.research_timeout_s
                    )
                except TimeoutError as e:
                    # The step's own name and depth, not the caller's blanket wait.
                    raise StepTimeout(
                        f"step {RESEARCH_STEP} research ({depth}) exceeded "
                        f"{self.d.research_timeout_s:.0f} s"
                    ) from e
            elapsed_ms = (time.perf_counter() - started) * 1000
            search_ms = timings.get("search_ms", 0.0)
            return {
                # The search provider's own calls, and the rest of the step (replayed fetches,
                # extraction, scoring) apart from them: neither is hidden in the other.
                "timings": {"search_ms": search_ms, "research_other_ms": elapsed_ms - search_ms},
                "research": result.report,
                "findings": result.findings,
                "_detail": f"{len(result.findings)} findings",
            }

        return self.stepped(RESEARCH_STEP, body)

    # ── step 7 ────────────────────────────────────────────────────────────────────────────────
    async def select_agents(self, state: RunState) -> dict[str, Any]:
        d0 = state.get("depth") == "D0"
        fan_out = [a for a in FAN_OUT_AGENTS if not (a == "A2" and d0)]
        # The rule and its reason are kept with the run (Phases.md Phase 7: persisted rationale).
        # A2 reads the pages research fetched, so it runs iff the depth is not D0; A1, A3, A6 and
        # A7 always run (A3's personas feed C and write-back; A6/A7 are the copy and its read).
        # At D0 the one fresh query's findings are in the run and reach A3 (as evidence titles);
        # A2 does not read them (PAGES_BY_DEPTH D0 = 0): a stated limit of the forced fresh slot.
        depth = state.get("depth", "D1")
        rationale = {
            "A2": "skipped: depth D0 reads no pages (its one fresh query feeds A3's evidence, "
            "not A2)"
            if d0
            else f"runs: depth {depth} fetched pages (reads {PAGES_BY_DEPTH[depth]})",
            "A1": "always runs",
            "A3": "always runs (its personas feed C and write-back)",
            "A6": "always runs",
            "A7": "always runs",
        }
        return {
            "selected_agents": [*fan_out, "A6", "A7"],
            "agent_rationale": rationale,
            "_detail": ", ".join(fan_out),
        }

    def fan_out(self, state: RunState) -> list[Send]:
        run = cast(RunState, dict(state))
        return [
            Send("run_agent", AgentTask(agent_id=a, run=run))
            for a in state["selected_agents"]
            if a in FAN_OUT_AGENTS
        ]

    async def run_agent(self, state: AgentTask) -> dict[str, Any]:
        agent_id, run = state["agent_id"], state["run"]
        try:
            ctx = await self.context(run, AGENTS_STEP)
            out = await self.d.agents[agent_id].run(run, ctx)
        except ABORTS:
            raise
        except Exception as e:
            log.warning("agent failed; the run continues with a gap", extra={"agent": agent_id})
            return {"errors": [_agent_error(AGENTS_STEP, agent_id, e)]}
        return {"agent_outputs": {agent_id: out.model_dump(mode="json", by_alias=True)}}

    async def agents_join(self, state: RunState) -> dict[str, Any]:
        outputs = state.get("agent_outputs", {})
        failed = sorted(e["agent"] for e in state.get("errors", []) if e.get("step") == AGENTS_STEP)
        return {
            "_detail": f"ran {', '.join(sorted(outputs)) or 'none'}"
            + (f"; failed {', '.join(failed)}" if failed else "")
        }

    # ── steps 8 to 11 ─────────────────────────────────────────────────────────────────────────
    async def personalise(self, state: RunState) -> dict[str, Any]:
        personas = state.get("agent_outputs", {}).get("A3", {}).get("personas", [])
        update: dict[str, Any] = {"personas": personas, "_detail": f"{len(personas)} personas"}
        if personas:
            # Now the persona exists: recall again so the M that reuse and write-back decisions use
            # has C (app/memory/recall.py). Replaces the persona-less hits of step 4.
            with collecting() as timings:
                found = await self.d.retrieve(state, [p["summary"] for p in personas])
            update.update(
                timings=timings,
                memory_hits=found.hits,
                memory_coverage_final=found.coverage,
                memory_slots={"filled": found.slots_filled, "of": found.slots_total},
                memory_persona_scored=True,
                _detail=f"{len(personas)} personas; memory re-scored with C: "
                f"{len(found.hits)} hits, coverage {found.slots_filled} of "
                f"{found.slots_total} slots",
            )
        return update

    async def build_strategy(self, state: RunState) -> dict[str, Any]:
        outputs = state.get("agent_outputs", {})
        gaps = [
            a for a in FAN_OUT_AGENTS if a in state.get("selected_agents", []) and a not in outputs
        ]
        return {
            "strategy": {
                "positioning": outputs.get("A1", {}).get("positioning"),
                "claims": outputs.get("A1", {}).get("claim_inventory", []),
                "personas": [p["id"] for p in state.get("personas", [])],
                "market": outputs.get("A2"),
                "gaps": gaps,
            }
        }

    async def generate_creative(self, state: RunState) -> dict[str, Any]:
        regenerating = state.get("creative") is not None
        round_ = state.get("revision_round", 0) + (1 if regenerating else 0)
        try:
            ctx = await self.context(state, CREATIVE_STEP, round_)
            out = await self.d.agents["A6"].run(state, ctx)
        except ABORTS:
            raise
        except Exception as e:
            return {
                "creative": None,
                "revision_round": round_,
                "errors": [_agent_error(CREATIVE_STEP, "A6", e)],
                "_detail": "A6 failed: no creative",
            }
        return {
            "creative": out.model_dump(mode="json", by_alias=True),
            "revision_round": round_,
            "approval": None,
            "_detail": f"draft {round_ + 1}",
        }

    def _gate_error(self, ctx: AgentContext, why: str) -> GateReport:
        sector = ctx.truth.sector.describe() if ctx.truth.sector is not None else "no sector"
        finding = Finding("gate_error", why)
        return GateReport((finding,), (), sector, self.d.lexicon.version, ctx.on)

    async def _gate(self, state: RunState) -> tuple[GateReport | None, ProductTruth]:
        """Step 15's view of the draft: the DETERMINISTIC gates over the statements step 11 already
        extracted, against the truth as it is now (a claim retired while the run waited is judged as
        retired). No model call: the extraction is not repeated. A draft whose extraction failed
        stays a `gate_error` block."""
        ctx = await self.context(state)
        creative = state.get("creative")
        if creative is None:
            return None, ctx.truth
        stored = (state.get("validation") or {}).get("statements")
        if stored is None:
            return self._gate_error(ctx, "the draft's statements were never extracted"), ctx.truth
        statements = [Statement(**s) for s in stored]
        report = evaluate(
            creative["copy"],
            statements,
            ctx.truth,
            self.d.lexicon,
            ctx.on,
            neutral_terms=naming_terms(state),
        )
        return report, ctx.truth

    async def validate_evidence(self, state: RunState) -> dict[str, Any]:
        """Step 11. A7's claim verification: the classifier EXTRACTS the statements from the copy (a
        model call, on the strong tier for a regulated sector: Rules.md T4) and the deterministic
        gates judge them. A7's own risk read follows, in the same execution (one execution id for
        both, migration 0035). A failed extraction is a `gate_error` block, as in the classifier; a
        budget refusal is not a block and ends the run partial."""
        creative = state.get("creative")
        errors: list[dict[str, Any]] = []
        revision_round = state.get("revision_round", 0)
        ctx = await self.context(state, VALIDATION_STEP, revision_round)
        validation: dict[str, Any]
        if creative is None:
            validation = {
                "outcome": "block",
                "statements": None,
                "findings": [
                    {
                        "kind": "no_creative",
                        "gate": "claim",
                        "outcome": "block",
                        "message": "no creative was generated (A6 failed)",
                        "refs": [],
                    }
                ],
            }
        else:
            statements: list[Statement] | None
            try:
                statements = await self.d.extract_statements(ctx, creative["copy"])
                report = evaluate(
                    creative["copy"],
                    statements,
                    ctx.truth,
                    self.d.lexicon,
                    ctx.on,
                    neutral_terms=naming_terms(state),
                )
            except ABORTS:
                raise
            except Exception as e:
                log.exception("claim extraction failed; blocking the copy")
                statements = None
                report = self._gate_error(
                    ctx, f"claim extraction failed, so the copy is unchecked: {type(e).__name__}"
                )
            if report.outcome == "block" and self.d.record_block is not None:
                # Blocked and logged (Rules.md T2): every blocked draft, every round, so the
                # findings outlive the run's state. A re-executed step may log a draft twice.
                await self.d.record_block(state, report)
            validation = report_dict(report)
            validation["statements"] = (
                None
                if statements is None
                else [
                    {
                        "text": s.text,
                        "claim_id": s.claim_id,
                        "labelled_deduction": s.labelled_deduction,
                    }
                    for s in statements
                ]
            )
        try:
            risk = await self.d.agents["A7"].run(state, ctx)
            # A model opinion, stored apart from `findings` and never counted in `outcome`.
            validation["advisory"] = risk.model_dump(mode="json")
        except ABORTS:
            raise
        except Exception as e:
            validation["advisory"] = None
            errors.append(_agent_error(VALIDATION_STEP, "A7", e))
        update: dict[str, Any] = {
            "validation": validation,
            "_detail": f"gates: {validation['outcome']}",
        }
        if errors:
            update["errors"] = errors
        return update

    def route_after_agents(self, state: RunState) -> str:
        """An outage gap in the specialist agents ends the run now: no persona, strategy or copy
        steps are spent on a run that is already invalid."""
        return "invalid" if invalidating_gaps(state.get("errors", [])) else "personalise"

    async def invalidate_infrastructure(self, state: RunState) -> dict[str, Any]:
        """Terminal. Marks the run for `Pipeline` to end `failed` as an infrastructure failure
        (`runs.failure.invalidated`), BEFORE it can reach the approval step, so no human ever sees
        the output of a run the provider's outage made. Nothing here reads the output."""
        gaps = invalidating_gaps(state.get("errors", []))
        return {
            "invalidated": {
                "kind": "infrastructure_gap",
                "infrastructure_failure": gaps[0]["infrastructure"],
                "gaps": [
                    {"step": g["step"], "agent": g["agent"], "error": g["error"]} for g in gaps
                ],
            }
        }

    def route_after_validation(self, state: RunState) -> str:
        if invalidating_gaps(state.get("errors", [])):
            return "invalid"
        v = state.get("validation") or {}
        if (
            v.get("outcome") == "block"
            and state.get("creative") is not None
            and state.get("revision_round", 0) < self.d.max_revisions
        ):
            return "generate_creative"
        return "await_human_approval"

    # ── steps 12 to 15 ────────────────────────────────────────────────────────────────────────
    async def await_human_approval(self, state: RunState) -> dict[str, Any]:
        approval = state.get("approval")
        if approval is None:
            raise BrandverseError("resumed at the approval step without a decision")
        return {"_detail": f"decision: {approval['decision']}"}

    def route_after_approval(self, state: RunState) -> str:
        approval = state.get("approval") or {}
        return "generate_creative" if approval.get("decision") == "revise" else "assemble_campaign"

    async def assemble_campaign(self, state: RunState) -> dict[str, Any]:
        decision = (state.get("approval") or {}).get("decision")
        return {
            "campaign": {
                "status": "approved" if decision == "approve" else "rejected",
                "strategy": state.get("strategy"),
                "creative": state.get("creative"),
                "validation": state.get("validation"),
                "gaps": [
                    {"agent": e["agent"], "step": e["step"], "error": e["error"]}
                    for e in state.get("errors", [])
                ],
            }
        }

    async def record_feedback(self, state: RunState) -> dict[str, Any]:
        approval = state.get("approval") or {}
        return {
            "feedback": {
                "decision": approval.get("decision"),
                "by": approval.get("by"),
                "reason": approval.get("reason"),
                "revision_round": state.get("revision_round", 0),
                "findings": len((state.get("validation") or {}).get("findings", [])),
            }
        }

    def write_memory_node(self) -> NodeFn:
        async def node(state: RunState, config: RunnableConfig) -> dict[str, Any]:
            await self.emit(state, MEMORY_STEP, StepStatus.STARTED)
            try:
                stage = config.get("configurable", {}).get("stage")
                if not isinstance(stage, MemoryStage):
                    raise BrandverseError("step 15 needs the run's MemoryStage in its config")
                report, truth = await self._gate(state)
                if report is None:
                    report = evaluate({}, [], truth, self.d.lexicon, self.d.today())
                with collecting() as timings:
                    memory = await self.d.write_memory(state, stage, report, truth)
            except Exception as e:
                await self.emit(
                    state, MEMORY_STEP, StepStatus.FAILED, f"{type(e).__name__}: {e}"[:300]
                )
                raise
            await self.emit(state, MEMORY_STEP, StepStatus.COMPLETED)
            return {"memory": memory, "timings": timings}

        return node
