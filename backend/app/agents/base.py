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


@dataclass(frozen=True, slots=True)
class AgentContext:
    run_id: UUID
    org_id: UUID
    truth: ProductTruth
    on: date


class Agent(Protocol):
    id: str

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel: ...
