"""The claim classifier (Phases.md Phase 3 chunk C): statements out of free copy, then the gates.

A model **extracts** the factual statements in generated copy — exact quotes — and names the
listed claim each rests on, or none (prompt `claim_extract@v2`; v2 also reports property
assertions inside noun phrases, threat V11). It never labels anything: the
claim gate computes verified / deduced / blocked from the cited claim's stored type
(app/core/gates.py). Every quote is checked against the copy, so an invented quote is a block,
not a pass.

**Rules.md T2 on the input side.** `truth_context` — the only view of a product's claims ever put
in front of a model, here and for Phase 6's copy writer — holds active verified and deduced claims
only. A restricted claim is never shown to a model, so a restricted claim in output has to
originate in the model, not be prompted by it (docs/evaluation-plan.md, V2 mitigation 1). The copy
is still scanned for every restricted claim's text by the risk gate.

**Rules.md T4.** Classifying regulated-sector output is claim verification on regulated-sector
output, so it runs on the strong tier; everything else on standard. With the strong tier switched
off (`llm_allow_strong_tier`, the default) the call is refused and the copy is blocked
(`gate_error`) — fail closed, never downgraded (C9's exception). This is the one routing rule
Phase 3 needs; the full router is Phase 7.

**Failure.** Any model failure — a refusal, a rate limit, invalid output after the one repair —
blocks the copy with `gate_error`; nothing reaches a reviewer unchecked.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.gates import (
    Finding,
    GateReport,
    Statement,
    Superlatives,
    evaluate,
    record_gate_block,
)
from app.core.truth import Claim, ProductTruth
from app.errors import BudgetExceeded
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

log = logging.getLogger(__name__)

CLAIM_EXTRACT = ("claim_extract", "v2")


class ExtractedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=2000)
    # Omitted, null or empty all mean "cites no listed claim" — which the claim gate blocks as
    # unsourced. The provider is sent this field as optional (app/llm/schema.py).
    claim_id: str | None = None
    labelled_deduction: bool = False

    @field_validator("claim_id")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ExtractedStatements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[ExtractedStatement] = Field(max_length=200)


def truth_context(truth: ProductTruth) -> tuple[Claim, ...]:
    """The product's claims a model may see: active verified and deduced — never restricted, never
    retired (Rules.md T2)."""
    return truth.active("verified") + truth.active("deduced")


def classifier_tier(truth: ProductTruth) -> tuple[Tier, str]:
    if truth.sector is not None and truth.sector.regulated:
        return Tier.STRONG, (
            "T4: claim verification on regulated-sector output, sector " + truth.sector.describe()
        )
    return Tier.STANDARD, "claim extraction: default tier"


def claims_block(claims: Sequence[Claim]) -> str:
    """The claims as the prompts list them: `id | type | text`, one per line."""
    if not claims:
        return "(none)"
    return "\n".join(f"{c.id} | {c.claim_type} | {c.text}" for c in claims)


async def extract_statements(
    gateway: LLMGateway,
    prompts: PromptLibrary,
    ctx: CallContext,
    copy: Mapping[str, str],
    truth: ProductTruth,
    *,
    tier: tuple[Tier, str] | None = None,
) -> list[Statement]:
    chosen, reason = tier or classifier_tier(truth)
    prompt = prompts.get(*CLAIM_EXTRACT).render(
        copy="\n\n".join(f"[{name}]\n{text}" for name, text in copy.items()),
        claims=claims_block(truth_context(truth)),
    )
    extracted = await gateway.structured(
        ExtractedStatements,
        tier=chosen,
        prompt=prompt,
        ctx=ctx,
        route_reason=reason,
        max_output_tokens=2048,
    )
    return [Statement(s.quote, s.claim_id, s.labelled_deduction) for s in extracted.statements]


async def check_copy(
    gateway: LLMGateway,
    prompts: PromptLibrary,
    ctx: CallContext,
    copy: Mapping[str, str],
    truth: ProductTruth,
    lexicon: Superlatives,
    on: date,
) -> GateReport:
    """Extract, then run every gate. A failed extraction is a `gate_error` block, never a pass."""
    try:
        statements = await extract_statements(gateway, prompts, ctx, copy, truth)
    except BudgetExceeded:
        raise  # the run's caps, not a fault of the copy: the run ends partial (Rules.md C4)
    except Exception as e:
        log.exception("claim extraction failed; blocking the copy")
        sector = truth.sector.describe() if truth.sector is not None else "no sector"
        finding = Finding(
            "gate_error", f"claim extraction failed, so the copy is unchecked: {type(e).__name__}"
        )
        return GateReport((finding,), (), sector, lexicon.version, on)
    return evaluate(copy, statements, truth, lexicon, on)


async def validate_output(
    gateway: LLMGateway,
    prompts: PromptLibrary,
    pool: AsyncConnectionPool[AsyncConnection],
    ctx: CallContext,
    *,
    product_id: UUID,
    copy: Mapping[str, str],
    truth: ProductTruth,
    lexicon: Superlatives,
    on: date,
) -> GateReport:
    """`check_copy`, and a blocked report appended to audit_log — blocked and logged (T2)."""
    report = await check_copy(gateway, prompts, ctx, copy, truth, lexicon, on)
    if report.outcome == "block":
        await record_gate_block(
            pool, org_id=ctx.org_id, product_id=product_id, run_id=ctx.run_id, report=report
        )
    return report
