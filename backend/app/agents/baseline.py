"""Baseline A's writer: ONE model call (evaluation arm `baseline_a`; decision-log 2.48).

Receives the brief INCLUDING the audience, the verified (and deduced) claims, and the brand's voice
attributes (owner, 2026-11-06). Does NOT receive research findings, memory, personas, positioning or
market analysis: those are products of the agents the comparison is about. The prompt
`baseline_a_copy@v1` carries A6's rules word for word (effort parity, recorded in the prompt file);
the draft becomes copy through the SAME function A6 uses, so the arms' copy is assembled
identically, and the same sector gates, validator and approval apply downstream.

Standard tier, like A6. A budget refusal is not caught here: it propagates and ends the run partial.
"""

from collections.abc import Mapping
from typing import Any

from app.agents.base import AgentContext
from app.agents.copywriter import CopyDraft, creative_from_draft, problems_text
from app.agents.outputs import CreativeOutput
from app.core.claim_classifier import claims_block, truth_context
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

BASELINE_A_COPY = ("baseline_a_copy", "v1")


class BaselineWriter:
    id = "BASELINE_A"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        product = state.get("product") or {}
        prompt = self._prompts.get(*BASELINE_A_COPY).render(
            claims=claims_block(truth_context(ctx.truth)),
            audience=state.get("audience") or "(none given)",
            voice=(product.get("voice") or "").strip() or "(none given)",
            brief=state.get("brief") or "(none given)",
            problems=problems_text(state.get("validation")),
        )
        draft = await self._gateway.structured(
            CopyDraft,
            tier=Tier.STANDARD,
            prompt=prompt,
            ctx=ctx.call_context(self.id),
            route_reason="Baseline A single call: default tier",
            max_output_tokens=1024,
        )
        return creative_from_draft(draft, ctx)
