"""A1, product and brand intelligence (Phases.md Phase 6 chunk B; prompt `a1_positioning@v1`).

The model proposes a one-line positioning and up to five differentiators, and nothing else:

- **It is shown only what it may use.** The product's active verified and deduced claims
  (`truth_context`), never a restricted or a retired one (Rules.md T2), the category, the sector
  and the brief. It is told to add no product fact.
- **Each differentiator names the claims it rests on, and the names are checked.** A differentiator
  citing a claim that was not in front of the model, or none, is dropped and counted
  (`dropped_unattributed`): an unattributed differentiator is an assertion about the product with no
  source.
- **The claim inventory is not the model's.** It is the product's active verified claim ids, taken
  from the truth by code, so a model can neither add a claim to the inventory nor leave one out.
- **No claims, no call.** With nothing it may use there is nothing to position; the output is empty
  and the run's copy is written from the brief alone.

What it does NOT do: the brand-voice vector (Architecture.md section 7 lists it for A1) is not built
here; the brand-voice gate's similarity score is a separate item, not yet scheduled in a chunk.
Positioning is model prose that A6 is shown; it is a proposal, and the gates at step 11 judge the
copy, not the positioning. Standard tier. A budget refusal propagates and ends the run partial.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.outputs import Differentiator, ProductBrandOutput
from app.core.claim_classifier import claims_block, truth_context
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

A1_POSITIONING = ("a1_positioning", "v1")
MAX_DIFFERENTIATORS = 5


class _RawPositioning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positioning: str = Field(min_length=1, max_length=200)
    differentiators: list[Differentiator] = Field(default_factory=list, max_length=8)


class ProductBrandAnalyst:
    id = "A1"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> ProductBrandOutput:
        inventory = [c.id for c in ctx.truth.active("verified")]
        shown = truth_context(ctx.truth)
        if not shown:
            return ProductBrandOutput(claim_inventory=inventory)
        product = state.get("product") or {}
        truth = state.get("truth") or {}
        raw = await self._gateway.structured(
            _RawPositioning,
            tier=Tier.STANDARD,
            prompt=self._prompts.get(*A1_POSITIONING).render(
                claims=claims_block(shown),
                category=product.get("category") or "(not given)",
                sector=(truth.get("sector") or "(not given)").replace("_", " "),
                brief=state.get("brief") or "(none given)",
            ),
            ctx=ctx.call_context(self.id),
            route_reason="A1 positioning: default tier",
            max_output_tokens=1024,
        )
        allowed = {c.id for c in shown}
        kept = [d for d in raw.differentiators if set(d.claim_ids) <= allowed]
        return ProductBrandOutput(
            positioning=" ".join(raw.positioning.split()),
            differentiators=kept[:MAX_DIFFERENTIATORS],
            claim_inventory=inventory,
            dropped_unattributed=len(raw.differentiators) - len(kept),
        )
