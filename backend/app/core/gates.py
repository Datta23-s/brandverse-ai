"""The Validation Engine's deterministic gates (Architecture.md §8; Phases.md Phase 3 chunk B).

Three gates judge generated copy against the product's truth and its sector's registry:

- **claim** — every factual statement is labelled from what it cites, never by a model:
  verified (an active verified claim of this product), deduced (an active deduced claim, and the
  statement is labelled as a deduction), otherwise blocked — unsourced, unknown, retired or
  restricted citations alike (Rules.md T1, T2, P9).
- **risk** — the whole copy is scanned: the product's restricted claims, every active
  prohibited phrasing of the sector, every applicable required disclaimer, superlatives without a
  cited source (T3), registry coverage (fail closed). The eight predatory anti-patterns (T5) are
  mapped below as blocking so no later detector can be wired as a warning; no detector for them
  is built in Phase 3.
- **brand_voice, registry half** — a required disclaimer reworded rather than omitted is its own
  finding (`altered_disclaimer`), and `voice_scoring_text` removes disclaimer text before Phase 6
  scores voice similarity. The similarity score itself is not built here.

**How a gate fails: blocked, never warned** (Rules.md T2, T5). `SEVERITY` maps every finding
kind to its outcome and cannot be changed at runtime; only `deduced_labelled` is a `flag` (a
labelled deduction is allowed, and shown to the reviewer); everything else blocks. An exception
anywhere in evaluation is itself a block (`gate_error`), never a pass. A block is logged and
appended to audit_log (`record_gate_block`).

Matching: prohibited phrases and restricted claims use the literal, case- and
punctuation-insensitive matcher (app/core/text_match.py); required disclaimers use the declared
verbatim rule (app/core/sector_registry.py, `verbatim_form`). A paraphrase of a restricted claim is
not caught here (docs/evaluation-plan.md, threat V2).
"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args
from uuid import UUID

import yaml
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import REPO_ROOT
from app.core.sector_registry import check_registry_coverage, verbatim_form
from app.core.text_match import contains_phrase, matches_rule, normalise
from app.core.truth import ProductTruth
from app.errors import RegistryRuleInvalid
from app.llm.prompts import file_sha256

log = logging.getLogger(__name__)

GateName = Literal["claim", "risk", "brand_voice"]
Outcome = Literal["pass", "flag", "block"]
FindingKind = Literal[
    # claim gate: statements, labelled from what they cite
    "statement_not_in_copy",
    "unsourced_claim",
    "cites_unknown_claim",
    "cites_retired_claim",
    "cites_restricted_claim",
    "deduced_unlabelled",
    "deduced_labelled",
    # risk gate: the whole copy
    "restricted_claim_in_copy",
    "prohibited_phrase",
    "missing_disclaimer",
    "unsupported_superlative",
    "registry_coverage",
    "registry_rule_invalid",
    # risk gate: the eight predatory anti-patterns (Rules.md T5) — blocking; no detector yet
    "false_scarcity",
    "fabricated_social_proof",
    "undisclosed_paid_endorsement",
    "vulnerability_targeting",
    "dark_pattern_consent",
    "unsubstantiated_health_claim",
    "fictional_price_anchoring",
    "confirmshaming",
    # brand-voice gate, registry half
    "altered_disclaimer",
    # any gate
    "gate_error",
]

# Every finding kind's outcome, fixed. A new kind with no entry fails the test suite.
SEVERITY: Mapping[FindingKind, Outcome] = MappingProxyType(
    {kind: ("flag" if kind == "deduced_labelled" else "block") for kind in get_args(FindingKind)}
)
GATE_OF: Mapping[FindingKind, GateName] = MappingProxyType(
    {
        kind: (
            "claim"
            if kind
            in (
                "statement_not_in_copy",
                "unsourced_claim",
                "cites_unknown_claim",
                "cites_retired_claim",
                "cites_restricted_claim",
                "deduced_unlabelled",
                "deduced_labelled",
                "gate_error",
            )
            else "brand_voice"
            if kind == "altered_disclaimer"
            else "risk"
        )
        for kind in get_args(FindingKind)
    }
)

SUPERLATIVES_DIR: Path = REPO_ROOT / "config" / "superlatives"


@dataclass(frozen=True, slots=True)
class Superlatives:
    version: str
    phrases: tuple[str, ...]


def load_superlatives(version: str = "v1", directory: Path = SUPERLATIVES_DIR) -> Superlatives:
    """A released superlative list; refused if its file no longer matches versions.lock."""
    path = directory / f"{version}.yaml"
    locked = {
        v: h
        for v, h in (
            line.split()
            for line in (directory / "versions.lock").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
    }
    if locked.get(version) != f"sha256:{file_sha256(path)}":
        raise ValueError(f"superlatives {version} does not match versions.lock")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("version") != version:
        raise ValueError(f"superlatives {path.name} declares version {data.get('version')!r}")
    return Superlatives(version, tuple(str(p) for p in data["phrases"]))


@dataclass(frozen=True, slots=True)
class Statement:
    """A factual statement in the copy and what it cites (chunk C's classifier extracts these)."""

    text: str  # an exact quote from the copy
    claim_id: str | None
    labelled_deduction: bool = False


@dataclass(frozen=True, slots=True)
class Resolution:
    """A statement that resolved to its source."""

    statement: str
    label: Literal["verified", "deduced"]
    claim_id: str
    evidence_url: str | None


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    message: str
    refs: tuple[str, ...] = ()

    @property
    def gate(self) -> GateName:
        return GATE_OF[self.kind]

    @property
    def outcome(self) -> Outcome:
        return SEVERITY[self.kind]


@dataclass(frozen=True, slots=True)
class GateReport:
    findings: tuple[Finding, ...]
    resolutions: tuple[Resolution, ...]
    # Every report naming a sector prints its rules' provenance (migration 0021).
    sector: str
    superlatives_version: str
    checked_on: date
    rules_applied: tuple[str, ...] = field(default=())

    @property
    def outcome(self) -> Outcome:
        outcomes = {f.outcome for f in self.findings}
        return "block" if "block" in outcomes else "flag" if "flag" in outcomes else "pass"

    def by_gate(self, gate: GateName) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.gate == gate)


def _copy_text(copy: Mapping[str, str]) -> str:
    return "\n".join(copy.values())


def _claim_gate(
    statements: Sequence[Statement], copy: str, truth: ProductTruth
) -> tuple[list[Finding], list[Resolution]]:
    findings: list[Finding] = []
    resolutions: list[Resolution] = []
    copy_form = verbatim_form(copy)
    for s in statements:
        quote = s.text[:80]
        if not s.text.strip() or verbatim_form(s.text) not in copy_form:
            findings.append(
                Finding(
                    "statement_not_in_copy", f"statement is not a quote from the copy: {quote!r}"
                )
            )
            continue
        if s.claim_id is None:
            findings.append(
                Finding("unsourced_claim", f"factual statement cites no claim: {quote!r}")
            )
            continue
        claim = truth.claim(s.claim_id)
        if claim is None:
            findings.append(
                Finding(
                    "cites_unknown_claim",
                    f"statement cites a claim this product does not have: {quote!r}",
                    (s.claim_id,),
                )
            )
        elif claim.retired:
            findings.append(
                Finding(
                    "cites_retired_claim",
                    f"statement cites a retired claim: {quote!r}",
                    (claim.id,),
                )
            )
        elif claim.claim_type == "restricted":
            findings.append(
                Finding(
                    "cites_restricted_claim",
                    f"statement rests on a restricted claim: {quote!r}",
                    (claim.id,),
                )
            )
        elif claim.claim_type == "deduced":
            if s.labelled_deduction:
                findings.append(
                    Finding("deduced_labelled", f"labelled deduction: {quote!r}", (claim.id,))
                )
                resolutions.append(Resolution(s.text, "deduced", claim.id, claim.evidence_url))
            else:
                findings.append(
                    Finding(
                        "deduced_unlabelled",
                        f"a deduction presented as fact (Rules.md P9): {quote!r}",
                        (claim.id,),
                    )
                )
        else:
            resolutions.append(Resolution(s.text, "verified", claim.id, claim.evidence_url))
    return findings, resolutions


def _superlatives(
    copy: str, statements: Sequence[Statement], truth: ProductTruth, lexicon: Superlatives
) -> list[Finding]:
    findings = []
    verified = {c.id: c for c in truth.active("verified")}
    for phrase in lexicon.phrases:
        if not contains_phrase(copy, phrase):
            continue
        supported = any(
            contains_phrase(s.text, phrase)
            and s.claim_id in verified
            and contains_phrase(verified[s.claim_id].text, phrase)
            for s in statements
        )
        if not supported:
            findings.append(
                Finding(
                    "unsupported_superlative",
                    f"superlative {phrase!r} with no verified claim that states it (Rules.md T3)",
                    (f"superlatives:{lexicon.version}:{phrase}",),
                )
            )
    return findings


def _registry(copy: str, truth: ProductTruth, on: date) -> tuple[list[Finding], tuple[str, ...]]:
    findings: list[Finding] = []
    coverage = check_registry_coverage(truth.sector, truth.rules, on)
    if coverage.blocked:
        findings.append(Finding("registry_coverage", coverage.reason))
    if truth.sector is None:
        return findings, ()
    described = truth.sector.describe()
    copy_form, copy_loose = verbatim_form(copy), normalise(copy)
    for rule in truth.rules:
        if rule.sector_id != truth.sector.id or not rule.is_active(on):
            continue
        try:
            if rule.kind == "prohibited_phrase" and rule.pattern is not None:
                if matches_rule(copy, rule.match_type, rule.pattern):
                    findings.append(
                        Finding(
                            "prohibited_phrase",
                            f"copy uses wording the registry prohibits in sector {described}",
                            (rule.id,),
                        )
                    )
            elif rule.kind == "required_disclaimer" and rule.disclaimer_text is not None:
                applies = rule.pattern is None or matches_rule(copy, rule.match_type, rule.pattern)
                if applies and verbatim_form(rule.disclaimer_text) not in copy_form:
                    loose = normalise(rule.disclaimer_text)
                    if loose and f" {loose} " in f" {copy_loose} ":
                        findings.append(
                            Finding(
                                "altered_disclaimer",
                                "a required disclaimer appears with its case or punctuation "
                                f"changed in sector {described}; it must be verbatim",
                                (rule.id,),
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                "missing_disclaimer",
                                f"a required disclaimer is missing in sector {described}",
                                (rule.id,),
                            )
                        )
        except RegistryRuleInvalid as e:
            log.error("registry rule cannot be applied", extra={"rule": rule.id, "error": str(e)})
            findings.append(
                Finding(
                    "registry_rule_invalid", f"registry rule cannot be applied: {e}", (rule.id,)
                )
            )
    return findings, coverage.active_rule_ids


def evaluate(
    copy: Mapping[str, str],
    statements: Sequence[Statement],
    truth: ProductTruth,
    lexicon: Superlatives,
    on: date,
) -> GateReport:
    """All three gates over the copy. Never raises: any error is a `gate_error` block."""
    sector = truth.sector.describe() if truth.sector is not None else "no sector"
    try:
        text = _copy_text(copy)
        claim_findings, resolutions = _claim_gate(statements, text, truth)
        restricted = [
            Finding(
                "restricted_claim_in_copy",
                "the copy states a claim recorded as restricted for this product (Rules.md T2)",
                (c.id,),
            )
            for c in truth.active("restricted")
            if contains_phrase(text, c.text)
        ]
        registry, rules = _registry(text, truth, on)
        findings = (
            claim_findings + restricted + registry + _superlatives(text, statements, truth, lexicon)
        )
        return GateReport(tuple(findings), tuple(resolutions), sector, lexicon.version, on, rules)
    except Exception as e:
        log.exception("gate evaluation failed; blocking")
        return GateReport(
            (Finding("gate_error", f"gate evaluation failed: {type(e).__name__}: {e}"),),
            (),
            sector,
            lexicon.version,
            on,
        )


def voice_scoring_text(copy: Mapping[str, str], truth: ProductTruth, on: date) -> str:
    """The copy with every active required disclaimer's verbatim text removed, for Phase 6's
    brand-voice similarity: prescribed wording must not count for or against the brand's voice."""
    text = verbatim_form(_copy_text(copy))
    for rule in truth.rules:
        if rule.kind == "required_disclaimer" and rule.disclaimer_text and rule.is_active(on):
            text = text.replace(verbatim_form(rule.disclaimer_text), " ")
    return verbatim_form(text)


async def record_gate_block(
    pool: AsyncConnectionPool[AsyncConnection],
    *,
    org_id: UUID,
    product_id: UUID,
    run_id: UUID | None,
    report: GateReport,
) -> None:
    """Append a blocked report to audit_log (actor: the validation engine), and log it."""
    details = {
        "product_id": str(product_id),
        "sector": report.sector,
        "superlatives_version": report.superlatives_version,
        "checked_on": report.checked_on.isoformat(),
        "findings": [
            {"kind": f.kind, "gate": f.gate, "outcome": f.outcome, "message": f.message,
             "refs": list(f.refs)}
            for f in report.findings
        ],
    }  # fmt: skip
    log.warning(
        "output blocked by the validation engine",
        extra={
            "run_id": str(run_id) if run_id else None,
            "kinds": sorted({f.kind for f in report.findings if f.outcome == "block"}),
        },
    )
    entity_type, entity_id = ("run", str(run_id)) if run_id else ("product", str(product_id))
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id, "
            "details) values (%s, 'service', 'validation_engine', 'output.blocked', %s, %s, %s)",
            (org_id, entity_type, entity_id, json.dumps(details)),
        )
