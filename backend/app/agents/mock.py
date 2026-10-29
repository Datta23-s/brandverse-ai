"""Deterministic stand-ins for A1, A2, A3, A6 and A7: no model call, no network (Phase 6 chunk A).

They exist so the graph can be built, resumed and tested end to end before any prompt is written.
Their outputs have the shape the real agents will return, but they say nothing a model would: A1
reads the product's active verified claims, A6 writes copy that restates exactly those claims (so
the real gates have something true to check), A7 reports no risk flags. Chunk B replaces them.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import Agent, AgentContext


class ProductBrandOutput(BaseModel):
    positioning: str
    differentiators: list[str] = Field(default_factory=list)
    claim_inventory: list[str] = Field(default_factory=list)  # active verified claim ids


class MarketOutput(BaseModel):
    category_conventions: list[str] = Field(default_factory=list)
    whitespace: list[str] = Field(default_factory=list)
    sources: int = 0


class Persona(BaseModel):
    id: str
    summary: str


class CustomerOutput(BaseModel):
    personas: list[Persona]


class CopyStatement(BaseModel):
    text: str
    claim_id: str | None
    labelled_deduction: bool = False


class CreativeOutput(BaseModel):
    copy_: dict[str, str] = Field(alias="copy")
    statements: list[CopyStatement]

    model_config = {"populate_by_name": True}


class RiskOutput(BaseModel):
    flags: list[str] = Field(default_factory=list)


class MockA1:
    id = "A1"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> ProductBrandOutput:
        claims = ctx.truth.active("verified")
        return ProductBrandOutput(
            positioning="mock positioning",
            differentiators=[c.text for c in claims],
            claim_inventory=[c.id for c in claims],
        )


class MockA2:
    id = "A2"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> MarketOutput:
        return MarketOutput(
            category_conventions=["mock convention"], sources=len(state.get("findings", []))
        )


class MockA3:
    id = "A3"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CustomerOutput:
        return CustomerOutput(personas=[Persona(id="p1", summary="mock persona")])


class MockA6:
    id = "A6"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        claims = ctx.truth.active("verified")
        text = " ".join(c.text.rstrip(".") + "." for c in claims)
        return CreativeOutput(
            copy={"headline": text},
            statements=[CopyStatement(text=c.text, claim_id=c.id) for c in claims],
        )


class MockA7:
    id = "A7"

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> RiskOutput:
        return RiskOutput()


def mock_agents() -> dict[str, Agent]:
    return {a.id: a for a in (MockA1(), MockA2(), MockA3(), MockA6(), MockA7())}
