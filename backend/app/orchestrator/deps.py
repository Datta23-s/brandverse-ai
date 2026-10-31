"""What the graph needs from outside, as ports.

Each is a callable the graph calls and a test replaces, so the graph itself touches no network, no
model and no database. The real adapters wrap the pieces that already exist; where the real piece is
not built yet (memory retrieval with `C` and `E`, the research-necessity engine) the port says so.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from app.agents.base import Agent, AgentContext, EvidenceReader
from app.core.gates import GateReport, Statement, Superlatives
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


# Step 11: the factual statements in a draft, extracted by the claim classifier (a model call made
# with the step's own execution context) and never supplied by the writer.
ExtractStatements = Callable[[AgentContext, Mapping[str, str]], Awaitable[list[Statement]]]
# Step 3's check of the brief: the same extraction as step 11, on the brief's own text.
CheckBrief = Callable[[AgentContext, Mapping[str, str]], Awaitable[list[Statement]]]
# Step 11: write a BLOCKED gate report to the audit log (Rules.md T2: blocked and logged).
RecordBlock = Callable[[RunState, GateReport], Awaitable[None]]
LoadContext = Callable[[UUID, UUID], Awaitable[ProductContext | None]]
# `personas` is None before A3 has spoken (C left out of every M) and A3's summaries after
# (app/memory/recall.py).
Retrieve = Callable[[RunState, Sequence[str] | None], Awaitable[MemoryResult]]
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
    extract_statements: ExtractStatements
    lexicon: Superlatives
    today: Callable[[], date]
    # Regeneration rounds after the first draft (Phases.md Phase 6 chunk B: bounded at 2).
    max_revisions: int = 2
    # How an agent reads a finding's text (agents/base.py). None: no text available to agents.
    evidence: EvidenceReader | None = None
    # None: the brief is not checked (`brief_check.status` is "skipped").
    check_brief: CheckBrief | None = None
    # None: blocked reports are kept in the run's state only.
    record_block: RecordBlock | None = None


async def no_memory(state: RunState, personas: Sequence[str] | None = None) -> MemoryResult:
    """No memory: a run with nothing to retrieve from says so (coverage 0), never a made-up hit.
    The real recall is `adapters.memory_recaller`; tests and scripts that need none use this."""
    return MemoryResult([], 0.0)
