"""The contract every specialist agent satisfies (Architecture.md §7).

An agent reads the run state and its context and returns a Pydantic model; it never writes the
state, so a failing agent leaves nothing half-written (the graph records the failure and the run
continues with a declared gap). A budget refusal is NOT an agent failure: `BudgetExceeded` is
re-raised by the graph so the run ends `partial` (Rules.md C4).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.core.truth import ProductTruth
from app.llm.ledger import CallContext


class EvidenceReader(Protocol):
    """How an agent reads the TEXT of a finding. State carries only what a finding is (url, title,
    score); the text is read here, from the page source the run was given: the evaluation
    snapshot under replay (the same bytes for every arm, and a miss is an error, never a live
    fetch), the page cache or the live fetcher otherwise. None when the page cannot be read or
    extracted."""

    async def text(self, url: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class AgentContext:
    run_id: UUID
    org_id: UUID
    truth: ProductTruth
    on: date
    # The graph step this execution belongs to, the revision round, and an id for THIS execution of
    # the step's body (new each time the body starts, so a re-run from a checkpoint gets another).
    step: int = 0
    revision_round: int = 0
    execution_id: UUID | None = None
    evidence: EvidenceReader | None = None

    def call_context(self, agent_id: str) -> CallContext:
        """The ledger context for a model call made by `agent_id` in this execution."""
        if self.execution_id is None or not 1 <= self.step <= 15:
            raise ValueError("an agent's calls need the graph step and execution it runs in")
        return CallContext(
            self.org_id, self.run_id, agent_id, self.step, self.revision_round, self.execution_id
        )


class Agent(Protocol):
    id: str

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel: ...
