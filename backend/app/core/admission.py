"""Run admission: pipeline steps 2 and 3 (PRD.md §5; Architecture.md §5.1, `validate_product_truth`
→ [blocked] → END). A run that fails any check does not start; it is blocked and logged (Rules.md
T2 — blocked, never warned).

A run is admitted only if all hold:
- the product has at least one active (unretired) verified claim (PRD.md §5 step 2;
  Phases.md Phase 3, "A product with no verified claims cannot start a run");
- the brand's sector is covered by the registry (sector_registry.check_registry_coverage: a brand
  with no sector, or a regulated sector with no active rules, blocks — fail closed);
- the brief does not request a restricted claim of the product, or wording a registry rule for
  the sector prohibits (PRD.md §5 step 3). Matching is deterministic (app/core/text_match.py); a
  paraphrase is not caught (docs/evaluation-plan.md, threat V2).

Every failing check is reported, not only the first, so the person starting the run sees all of
it at once. `decide_admission` is pure; `load_admission` reads its inputs as the signed-in caller
(row-level security applies, every query names the org); `record_block` appends the audit row as
the backend's own role, since API roles cannot write audit_log (0008).

The fake run (Phase 1) is admitted through this module from Phase 3. Phase 6's `POST /api/v1/runs`
must call the same functions, after inserting the runs row, and end a refused run with status
'blocked' (migration 0015) so every gate-blocked run is counted per arm.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.sector_registry import Sector, SectorConstraint, check_registry_coverage
from app.core.text_match import contains_phrase, matches_rule
from app.core.truth import load_product_truth
from app.errors import RegistryRuleInvalid

log = logging.getLogger(__name__)

BlockCode = Literal[
    "no_verified_claims",
    "registry_coverage",
    "registry_rule_invalid",
    "restricted_claim_requested",
    "prohibited_phrase_requested",
]


@dataclass(frozen=True, slots=True)
class RestrictedClaim:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class BlockReason:
    code: BlockCode
    message: str
    # The claim or registry rule ids behind the block.
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    reasons: tuple[BlockReason, ...]
    active_rule_ids: tuple[str, ...] = field(default=())

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True, slots=True)
class AdmissionInputs:
    sector: Sector | None
    verified_claims: int
    restricted: tuple[RestrictedClaim, ...]
    rules: tuple[SectorConstraint, ...]


def decide_admission(inputs: AdmissionInputs, brief: str | None, on: date) -> AdmissionDecision:
    reasons: list[BlockReason] = []
    if inputs.verified_claims < 1:
        reasons.append(
            BlockReason(
                "no_verified_claims",
                "the product has no active verified claim; enter one with its evidence first",
            )
        )
    coverage = check_registry_coverage(inputs.sector, inputs.rules, on)
    if coverage.blocked:
        reasons.append(BlockReason("registry_coverage", coverage.reason))

    if brief:
        hits = [c.id for c in inputs.restricted if contains_phrase(brief, c.text)]
        if hits:
            reasons.append(
                BlockReason(
                    "restricted_claim_requested",
                    "the brief asks for a claim recorded as restricted for this product",
                    tuple(hits),
                )
            )
        reasons.extend(_prohibited_in_brief(inputs, brief, on))
    return AdmissionDecision(tuple(reasons), coverage.active_rule_ids)


def _prohibited_in_brief(inputs: AdmissionInputs, brief: str, on: date) -> list[BlockReason]:
    if inputs.sector is None:
        return []
    hits: list[str] = []
    invalid: list[str] = []
    for rule in inputs.rules:
        if (
            rule.kind != "prohibited_phrase"
            or rule.sector_id != inputs.sector.id
            or not rule.is_active(on)
            or rule.pattern is None
        ):
            continue
        try:
            if matches_rule(brief, rule.match_type, rule.pattern):
                hits.append(rule.id)
        except RegistryRuleInvalid as e:
            log.error("registry rule cannot be applied", extra={"rule": rule.id, "error": str(e)})
            invalid.append(rule.id)
    out: list[BlockReason] = []
    if hits:
        out.append(
            BlockReason(
                "prohibited_phrase_requested",
                "the brief uses wording the registry prohibits in sector "
                + inputs.sector.describe(),
                tuple(hits),
            )
        )
    if invalid:
        out.append(
            BlockReason(
                "registry_rule_invalid",
                "a registry rule for this sector cannot be applied, so the brief cannot be cleared",
                tuple(invalid),
            )
        )
    return out


async def load_admission(
    conn: AsyncConnection, org_id: UUID, product_id: UUID
) -> AdmissionInputs | None:
    """A product's admission inputs, read as the caller. None if the org has no such product."""
    truth = await load_product_truth(conn, org_id, product_id)
    if truth is None:
        return None
    restricted = tuple(RestrictedClaim(c.id, c.text) for c in truth.active("restricted"))
    return AdmissionInputs(truth.sector, len(truth.active("verified")), restricted, truth.rules)


async def record_block(
    pool: AsyncConnectionPool[AsyncConnection],
    *,
    org_id: UUID,
    product_id: UUID,
    user_id: UUID,
    mode: str,
    decision: AdmissionDecision,
) -> None:
    """Append the block to audit_log (actor: this service; the requesting user in details)."""
    details = {
        "user_id": str(user_id),
        "mode": mode,
        "reasons": [
            {"code": r.code, "message": r.message, "refs": list(r.refs)} for r in decision.reasons
        ],
    }
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id, "
            "details) values (%s, 'service', 'run_admission', 'run.blocked', 'product', %s, %s)",
            (org_id, str(product_id), json.dumps(details)),
        )


def summarise(reasons: Sequence[BlockReason]) -> str:
    return "run blocked: " + "; ".join(r.message for r in reasons)
