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
    request: dict[str, Any]
    product: dict[str, Any] | None
    truth: dict[str, Any] | None  # a summary: sector, counts. Not the claims.
    blocked: list[dict[str, Any]] | None  # admission's reasons; set => the run ends `blocked`
    memory_hits: list[dict[str, Any]]
    memory_coverage: float  # 0..1
    research_score: float  # R
    depth: Depth
    research: dict[str, Any] | None
    findings: Annotated[list[dict[str, Any]], add]
    selected_agents: list[str]
    agent_outputs: Annotated[dict[str, Any], merge_dicts]
    personas: list[dict[str, Any]]
    strategy: dict[str, Any] | None
    creative: dict[str, Any] | None
    validation: dict[str, Any] | None
    revision_round: int
    approval: dict[str, Any] | None  # {"decision": Decision, "by": str, "reason": str | None}
    campaign: dict[str, Any] | None
    feedback: dict[str, Any] | None
    memory: dict[str, Any] | None
    budget: dict[str, Any]
    errors: Annotated[list[dict[str, Any]], add]


class AgentTask(TypedDict):
    """What one concurrent agent branch receives (LangGraph `Send`)."""

    agent_id: str
    run: RunState
