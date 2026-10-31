"""A7's risk read (Phases.md Phase 6 chunk B; prompt `a7_risk@v1`): a MODEL OPINION, NOT A
DETECTOR.

Rules.md T5 asks for the eight predatory anti-patterns to be BLOCKED, and it stays unmet: no
detector exists (threat V3), and a model's opinion is not one. So this read is advisory (owner,
2026-10-29):

- it never blocks and never changes a gate result or the outcome;
- it is returned as its own object, `RiskAdvisory` (`source: model_opinion`,
  `advisory_only: true`), and the graph stores it under `validation["advisory"]`, never inside
  `validation["findings"]`, so an approver and a reader can always tell a deterministic gate
  result from a model's opinion;
- its accuracy is unmeasured. Having it does not measure the approver's miss rate and does not
  reduce it by any known amount.

It reads only the copy, not the product's claims. Each flag must quote the copy exactly: a flag
whose quote is not in the copy is dropped and counted (`dropped_unverifiable`), because an
invented quote shown to a person reads as evidence. Standard tier. A budget refusal propagates
and ends the run partial; any other failure is a declared gap of A7 (nodes.py), never a block.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.outputs import RiskAdvisory, RiskFlag
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

A7_RISK = ("a7_risk", "v2")


class _RawFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flags: list[RiskFlag] = Field(default_factory=list, max_length=12)


def copy_text(copy: Mapping[str, str]) -> str:
    return "\n\n".join(f"[{name}]\n{text}" for name, text in copy.items())


class RiskReader:
    id = "A7"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> RiskAdvisory:
        copy = (state.get("creative") or {}).get("copy") or {}
        if not copy:
            return RiskAdvisory()
        raw = await self._gateway.structured(
            _RawFlags,
            tier=Tier.STANDARD,
            prompt=self._prompts.get(*A7_RISK).render(copy=copy_text(copy)),
            ctx=ctx.call_context(self.id),
            route_reason="A7 risk read: advisory, default tier",
            max_output_tokens=1024,
        )
        whole = "\n".join(copy.values())
        kept = [f for f in raw.flags if f.quote and f.quote in whole]
        return RiskAdvisory(flags=kept, dropped_unverifiable=len(raw.flags) - len(kept))
