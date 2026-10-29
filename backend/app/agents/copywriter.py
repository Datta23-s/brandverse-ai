"""A6, the copy writer (Phases.md Phase 6 chunk B; prompt `a6_copy@v1`).

The model writes a headline, a body and a call to action and NOTHING ELSE:

- **It is shown only what it may use.** The product's active verified and deduced claims
  (`truth_context`), never a restricted or a retired one (Rules.md T2). A restricted claim in its
  output therefore has to originate in the model, not be prompted by it.
- **It never says what it relied on.** The claim classifier extracts the statements from the copy at
  step 11 and the gates judge them; a writer that listed its own sources could list the wrong ones.
- **It never writes a disclaimer.** The sector's required disclaimers come from the registry and
  are APPENDED here, by code, verbatim, as the `footer` (Architecture.md section 8.1: required
  text is enforced by rule, never LLM-generated). A model that paraphrased one would be
  `altered_disclaimer`.
- **A rejected draft is described generically.** On a revision round the model is told the KIND of
  each problem in fixed words, never the finding's message or the draft's text: a message can quote
  the copy, and handing a restricted phrase the model itself wrote back to it would be prompting it.
  `PROBLEM_WORDS` has an entry for every finding kind (a test fails when a new kind has none).
- **Standard tier.** The creative agent is Standard (Architecture.md section 7); only claim
  verification on regulated output goes to the strong tier (T4), and that is the classifier's call.

A budget refusal is not caught here: it propagates and ends the run partial.
"""

from collections.abc import Mapping
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.outputs import CreativeOutput
from app.core.claim_classifier import claims_block, truth_context
from app.core.gates import FindingKind, required_disclaimers
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

A6_COPY = ("a6_copy", "v1")

PROBLEM_WORDS: Mapping[FindingKind, str] = {
    "statement_not_in_copy": "a statement was reported that is not in the copy",
    "unsourced_claim": "a factual statement was not backed by any listed claim",
    "cites_unknown_claim": "a statement leaned on a claim that is not listed",
    "cites_retired_claim": "a statement leaned on a claim that is no longer valid",
    "cites_restricted_claim": "a statement leaned on a claim that must not be used",
    "deduced_unlabelled": "an inference was presented as established fact",
    "deduced_labelled": "an inference was used (acceptable if clearly labelled as one)",
    "unrepeated_quantity": "a figure was used without repeating exactly what the claim says",
    "restricted_claim_in_copy": "the copy states something that must not be claimed",
    "prohibited_phrase": "the copy uses wording that is prohibited in this sector",
    "missing_disclaimer": "a required notice was missing",
    "unsupported_superlative": "the copy makes a superlative or ranking claim it cannot support",
    "context_superlative": "the copy uses a superlative word; keep it out of any factual statement",
    "registry_coverage": "the sector's rules could not be applied",
    "registry_rule_invalid": "a sector rule could not be applied",
    "altered_disclaimer": "a required notice was changed from its exact wording",
    "gate_error": "the draft could not be checked",
}
if set(PROBLEM_WORDS) != set(get_args(FindingKind)):  # a new finding kind needs its words
    raise RuntimeError("PROBLEM_WORDS must have an entry for every finding kind")


class CopyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=1500)
    call_to_action: str = Field(default="", max_length=150)


def problems_text(validation: Mapping[str, Any] | None) -> str:
    """What the previous draft is told: one line per distinct finding kind, in fixed words."""
    kinds = sorted({f["kind"] for f in (validation or {}).get("findings", [])})
    if not kinds:
        return "(none)"
    return "\n".join(f"- {PROBLEM_WORDS.get(k, 'a check flagged the draft')}" for k in kinds)


class CopyWriter:
    id = "A6"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        strategy = state.get("strategy") or {}
        personas = [p.get("summary", "") for p in state.get("personas", []) if p.get("summary")]
        prompt = self._prompts.get(*A6_COPY).render(
            claims=claims_block(truth_context(ctx.truth)),
            positioning=strategy.get("positioning") or "(none given)",
            audience="; ".join(personas) or "(none given)",
            brief=state.get("brief") or "(none given)",
            problems=problems_text(state.get("validation")),
        )
        draft = await self._gateway.structured(
            CopyDraft,
            tier=Tier.STANDARD,
            prompt=prompt,
            ctx=ctx.call_context(self.id),
            route_reason="A6 creative: default tier",
            max_output_tokens=1024,
        )
        copy = {
            name: text.strip()
            for name, text in (
                ("headline", draft.headline),
                ("body", draft.body),
                ("call_to_action", draft.call_to_action),
            )
            if text.strip()
        }
        notices = required_disclaimers(copy, ctx.truth, ctx.on)
        if notices:
            copy["footer"] = "\n".join(notices)
        return CreativeOutput(copy=copy)
