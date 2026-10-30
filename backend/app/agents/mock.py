"""Deterministic stand-ins for A1, A2, A3, A6 and A7: no model call, no network (Phase 6 chunk A).

They exist so the graph can be built, resumed and tested end to end before any prompt is written.
Their outputs have the shape the real agents will return, but they say nothing a model would: A1
reads the product's active verified claims, A6 writes copy that restates exactly those claims (so
the real gates have something true to check), A7 reports no risk flags. Chunk B replaces them.
"""

from collections.abc import Mapping
from typing import Any

from app.agents.base import Agent, AgentContext
from app.agents.outputs import (
    CreativeOutput,
    CustomerOutput,
    Differentiator,
    MarketItem,
    MarketOutput,
    Persona,
    ProductBrandOutput,
    RiskAdvisory,
)
from app.core.gates import Statement


class MockA1:
    id = "A1"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> ProductBrandOutput:
        claims = ctx.truth.active("verified")
        return ProductBrandOutput(
            positioning="mock positioning",
            differentiators=[Differentiator(text=c.text, claim_ids=[c.id]) for c in claims],
            claim_inventory=[c.id for c in claims],
        )


class MockA2:
    id = "A2"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> MarketOutput:
        return MarketOutput(
            category_conventions=[
                MarketItem(
                    text="mock convention", source_url="https://example.invalid/", quote="mock"
                )
            ],
            pages_read=len(state.get("findings", [])),
        )


class MockA3:
    id = "A3"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CustomerOutput:
        return CustomerOutput(personas=[Persona(id="p1", summary="mock persona")])


class MockA6:
    id = "A6"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        claims = ctx.truth.active("verified")
        return CreativeOutput(copy={"headline": " ".join(c.text.rstrip(".") + "." for c in claims)})


async def lexical_extractor(ctx: AgentContext, copy: Mapping[str, str]) -> list[Statement]:
    """A stand-in for the claim classifier: a statement for each active verified claim whose text
    appears in the copy, citing that claim. No model, deterministic."""
    text = "\n".join(copy.values())
    return [Statement(c.text, c.id) for c in ctx.truth.active("verified") if c.text in text]


class MockA7:
    id = "A7"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> RiskAdvisory:
        return RiskAdvisory()


def mock_agents() -> dict[str, Agent]:
    return {a.id: a for a in (MockA1(), MockA2(), MockA3(), MockA6(), MockA7())}
