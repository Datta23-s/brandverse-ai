"""The run state (Architecture.md §5.2) and its reducers.

Everything in it is plain data, because the checkpoint serializer runs in strict mode
(`app/orchestrator/__init__.py`): ids travel as strings, dataclasses as dicts. What is NOT in it on
purpose: the product truth. It is reloaded where the gates need it, so a claim retired while a run
waits days for its approver is judged against what is true when the gates run, not when the run
began.

`findings`, `agent_outputs` and `errors` have reducers because agents run concurrently. Without
them, parallel writes to the same key raise `InvalidUpdateError` (Architecture.md §5.2).
"""

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from app.core.timing import merge_timings

Depth = Literal["D0", "D1", "D2"]
Decision = Literal["approve", "revise", "reject"]


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Later keys win; each concurrent agent writes its own key, so nothing is overwritten."""
    return {**(left or {}), **(right or {})}


class RunState(TypedDict, total=False):
    run_id: str
    org_id: str
    user_id: str
    product_id: str
    brief: str | None
    # The audience the request states (the evaluation's `audience_hint`). Research is planned from
    # it, not from A3's personas, which come after research (a design limit, Phases.md Phase 6).
    audience: str | None
    request: dict[str, Any]
    # Step 3: the brief's property assertions and which of them no listed claim backs. Flagged and
    # recorded, never a block (owner, 2026-10-30); `status` says whether the check ran.
    brief_check: dict[str, Any] | None
    product: dict[str, Any] | None
    truth: dict[str, Any] | None  # a summary: sector, counts. Not the claims.
    blocked: list[dict[str, Any]] | None  # admission's reasons; set => the run ends `blocked`
    memory_hits: list[dict[str, Any]]
    memory_coverage: float  # 0..1; step 4, C left out (no persona yet)
    memory_slots: dict[str, int]  # {"filled": k, "of": N}: coverage is always "k of N slots"
    memory_coverage_final: float  # step 8, with the personas, when A3 spoke
    memory_similarities: list[float]  # S of each recalled item, step 4: the engine's quantity
    memory_persona_scored: bool  # the hits in `memory_hits` were scored with C
    research_score: float  # R
    depth: Depth
    # "requested": the run does the depth the request named (baselines, tuning, the stage-2 runs).
    # "necessity": the engine decides (the Proposed arm, production). Absent means requested.
    depth_policy: Literal["requested", "necessity"]
    # Step 5's decision, whichever policy: R, N, U, n, the depth R advised and the depth used.
    necessity: dict[str, Any] | None
    research: dict[str, Any] | None
    findings: Annotated[list[dict[str, Any]], add]
    selected_agents: list[str]
    agent_rationale: dict[str, str]  # why each pool agent ran or did not (step 7)
    agent_outputs: Annotated[dict[str, Any], merge_dicts]
    personas: list[dict[str, Any]]
    strategy: dict[str, Any] | None
    # Step 9: what memory injects into the copy writer, with each item's band (injection.py).
    injection: dict[str, Any] | None
    creative: dict[str, Any] | None
    validation: dict[str, Any] | None
    revision_round: int
    approval: dict[str, Any] | None  # {"decision": Decision, "by": str, "reason": str | None}
    campaign: dict[str, Any] | None
    feedback: dict[str, Any] | None
    memory: dict[str, Any] | None
    budget: dict[str, Any]
    errors: Annotated[list[dict[str, Any]], add]
    # Set by the terminal `invalidate_infrastructure` node when an agent gap is the provider's
    # outage: the run ends `failed`, invalidated, before any human sees its output (2026-11-05).
    invalidated: dict[str, Any] | None
    # Where the run's LOCAL time went, by name (app/core/timing.py; migration 0044). Figures of the
    # same name add: a node that runs twice (step 4 and step 8 recall, a re-run from a checkpoint)
    # spent the time twice.
    timings: Annotated[dict[str, float], merge_timings]


class AgentTask(TypedDict):
    """What one concurrent agent branch receives (LangGraph `Send`)."""

    agent_id: str
    run: RunState
