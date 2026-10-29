"""What the graph needs from outside, as ports.

Each is a callable the graph calls and a test replaces, so the graph itself touches no network, no
model and no database. The real adapters wrap the pieces that already exist; where the real piece is
not built yet (memory retrieval with `C` and `E`, the research-necessity engine) the port says so.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from app.agents.base import Agent
from app.core.gates import GateReport, Superlatives
from app.core.truth import ProductTruth
from app.memory.writeback import MemoryStage
from app.models.runs import RunEvent
from app.orchestrator.state import Depth, RunState


class EventSink(Protocol):
    async def append(self, event: RunEvent) -> str: ...


@dataclass(frozen=True, slots=True)
class ProductContext:
    product: dict[str, Any]  # id, brand_id, name, category
    truth: ProductTruth


@dataclass(frozen=True, slots=True)
class MemoryResult:
    hits: list[dict[str, Any]]
    coverage: float  # fraction of required knowledge slots filled at M >= 0.80


@dataclass(frozen=True, slots=True)
class ResearchResult:
    findings: list[dict[str, Any]]  # accepted, scored, attributed: never a page body
    report: dict[str, Any]


LoadContext = Callable[[UUID, UUID], Awaitable[ProductContext | None]]
Retrieve = Callable[[RunState], Awaitable[MemoryResult]]
Research = Callable[[RunState, Depth], Awaitable[ResearchResult]]
# Step 15: stage what the approved campaign adds to memory. Receives the run's own MemoryStage.
WriteMemory = Callable[[RunState, MemoryStage, GateReport, ProductTruth], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class GraphDeps:
    events: EventSink
    load_context: LoadContext
    retrieve: Retrieve
    research: Research
    agents: Mapping[str, Agent]
    write_memory: WriteMemory
    lexicon: Superlatives
    today: Callable[[], date]
    # Regeneration rounds after the first draft (Phases.md Phase 6 chunk B: bounded at 2).
    max_revisions: int = 2


async def no_memory(state: RunState) -> MemoryResult:
    """Retrieval is wired in chunk C, where `C` and `E` enter the M-score: until then a run
    retrieves nothing and says so (coverage 0), never a made-up hit."""
    return MemoryResult([], 0.0)
