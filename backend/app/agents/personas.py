"""A3, the customer-behaviour agent (Phases.md Phase 6 chunk B; prompt `a3_personas@v1`).

It proposes up to three customer personas as WORKING HYPOTHESES. Nothing it returns is a fact about
the product: it is shown only the product's active verified and deduced claims (Rules.md T2) and
told to add none. Each persona's one-line `summary` is what the local encoder turns into the persona
vector that chunk C compares with a memory item's (Memory.md section 3.1), so the summary is the
contract and is bounded (240 characters).

What it is shown beyond the claims: the product's category and sector, the brief, and the TITLES of
the findings step 6 returned. Titles only: page text is third-party prose and reaches an agent via
`ctx.evidence`, which A3 does not need (app/agents/base.py). Ids are assigned here, in order, never
by the model. Standard tier. A budget refusal propagates and ends the run partial.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.outputs import CustomerOutput, Persona
from app.core.claim_classifier import claims_block, truth_context
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

A3_PERSONAS = ("a3_personas", "v1")
MAX_TITLES = 10


class _RawPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=240)
    jobs_to_be_done: list[str] = Field(default_factory=list, max_length=3)
    objections: list[str] = Field(default_factory=list, max_length=3)
    channels: list[str] = Field(default_factory=list, max_length=3)


class _RawPersonas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    personas: list[_RawPersona] = Field(min_length=1, max_length=3)


def evidence_titles(findings: list[Mapping[str, Any]]) -> str:
    titles = [str(f["title"]).strip() for f in findings if f.get("title")][:MAX_TITLES]
    return "\n".join(f"- {t}" for t in titles) or "(none)"


class PersonaAnalyst:
    id = "A3"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CustomerOutput:
        product = state.get("product") or {}
        truth = state.get("truth") or {}
        prompt = self._prompts.get(*A3_PERSONAS).render(
            claims=claims_block(truth_context(ctx.truth)),
            category=product.get("category") or "(not given)",
            sector=(truth.get("sector") or "(not given)").replace("_", " "),
            brief=state.get("brief") or "(none given)",
            evidence=evidence_titles(list(state.get("findings", []))),
        )
        raw = await self._gateway.structured(
            _RawPersonas,
            tier=Tier.STANDARD,
            prompt=prompt,
            ctx=ctx.call_context(self.id),
            route_reason="A3 personas: default tier",
            max_output_tokens=1024,
        )
        return CustomerOutput(
            personas=[
                Persona(
                    id=f"p{i}",
                    summary=" ".join(p.summary.split()),
                    jobs_to_be_done=p.jobs_to_be_done,
                    objections=p.objections,
                    channels=p.channels,
                )
                for i, p in enumerate(raw.personas, start=1)
            ]
        )
