"""Memory write-back, step 15 (Memory.md §6): what may be written, and how it lands.

A run's memory is staged in this process and committed only in the transaction that writes the
run's terminal status, for a completed run (app/core/budgeted_run.py; docs/evaluation-plan.md §2).
Write-back has two halves to match:

1. **Screening, when staging** (`stage_writeback`). Each candidate is checked against what the run
   knows — its gate report and its product's truth — and against Memory.md §6.2. A candidate that
   fails is refused with its reasons and never staged. The rest are embedded as documents in one
   batch and staged with the encoder's id and revision (Memory.md §5). The stored content is
   exactly the text embedded: whitespace collapsed, case kept (the pinned model's tokenizer is
   uncased, so lowercasing would change nothing it sees).
2. **Commit, inside the terminal transaction** (`commit_memory`, called by `end_run`). What can
   change between staging and the end is checked against the database there: the run's campaign
   is approved by a human (§6.1 rule 1; the row is locked for share until commit), and each
   backing claim is still verified and active. Then near-duplicates (§6.1 rule 4).

**Eligibility as built (Memory.md §6.1, §6.2):**

- *Campaign approved by a human* (commit): the run has a campaign with `status = 'approved'`;
  else the commit is refused, the transaction rolls back and the run ends `failed`.
- *Passed the claim gate* (staging): the candidate rests on a claim the run's gate report resolved
  as `verified`, with no finding against that claim (so a V4 `unrepeated_quantity` flag keeps it
  out), in a report that did not block.
- *`deduced` with a scored source*: refused — no source is scored until Phase 5, so no deduced
  candidate qualifies yet.
- *Generalises* (staging, partly): refused if it states a price, a concrete discount, an explicit
  date or a promo code (§6.2's ephemera); campaign-specific in any other way is NOT detected.
- *Not consumer PII* (staging, partly): refused if it contains an email address or a
  phone-number-shaped digit run; other PII is NOT detected.
- *Not a raw page body* (staging): refused above `max_item_tokens` (one item is one chunk; longer
  content is refused, not split).
- *Not a near-duplicate* (commit): below.

**Near-duplicates (§6.1 rule 4).** Within the organisation, the same brand (or both org-level),
the same kind and the same encoder revision (vectors from different encoders are not
comparable), excluding seed items (§7.1: seed memory stays apart from what runs write), the
nearest item with cosine similarity above `near_duplicate_cosine` decides:

- a **revoked** near-duplicate: the candidate is skipped. Revocation is permanent (§6.4), and
  writing the same fact again under a new id would undo it.
- a live near-duplicate this write-back already inserted or reinforced: skipped, so one campaign
  counts once.
- any other live near-duplicate: **reinforced** instead of inserted — `last_used_at = now()`,
  `confidence = min(cap, confidence + step)` (§6.3's numbers; a null confidence stays null).

The search is exact (a filtered scan, not the approximate HNSW index), so a near-duplicate is
never missed because the index did not visit it. Write-backs of one organisation are serialised by
a transaction-level advisory lock, so two runs committing at once cannot both insert the same
fact. Every commit appends one `audit_log` row naming what was inserted, reinforced and skipped.
"""

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection

from app.core.gates import GateReport
from app.core.truth import ProductTruth
from app.memory.embeddings import Encoder
from app.memory.scoring import Writeback, load_scoring

log = logging.getLogger(__name__)

MemoryKind = Literal["persona", "competitor", "insight", "claim", "creative", "sector"]


class WritebackEncoder(Encoder, Protocol):
    def count_tokens(self, text: str) -> int: ...


class WritebackRefused(RuntimeError):
    """The commit must not happen: raised inside the terminal transaction, which it rolls back."""


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    """One memory_items row, screened and embedded, written if the run qualifies at its end."""

    org_id: UUID
    kind: str
    content: str
    provenance: str
    embedding: tuple[float, ...]
    embedding_model: str
    embedding_version: str
    brand_id: UUID | None = None
    product_id: UUID | None = None
    claim_id: UUID | None = None
    sector: str | None = None
    source_url: str | None = None
    confidence: float | None = None
    freshness_days: int | None = None


class MemoryStage:
    """The run's staged memory writes: in this process only, committed by `end_run` or lost."""

    def __init__(self) -> None:
        self._items: list[MemoryWrite] = []

    def stage(self, item: MemoryWrite) -> None:
        self._items.append(item)

    @property
    def items(self) -> tuple[MemoryWrite, ...]:
        return tuple(self._items)


# ── Screening ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fact a run proposes to remember, and the claim it rests on (§6.1 rule 2)."""

    kind: MemoryKind
    content: str
    claim_id: str | None
    confidence: float | None = None
    freshness_days: int | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    content: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Screening:
    staged: tuple[MemoryWrite, ...]
    refused: tuple[Refusal, ...]


_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"

# Each pattern names the form it catches; a fact that is ephemeral or personal in another form
# passes these and stays with the approver.
_SCREENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "states_a_price",
        re.compile(
            r"(?:₹|\$|€|£|\b(?:rs|inr|usd|eur|gbp)\.?)\s?\d"
            r"|\d[\d,]*(?:\.\d+)?\s?(?:₹|\b(?:rs|inr|usd|eur|gbp|rupees?|dollars?|euros?)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "states_a_discount",
        re.compile(
            r"\d+(?:\.\d+)?\s?%\s?(?:off|discount|cashback)\b|\bflat\s+\d|\bbogo\b"
            r"|\bbuy\s+\d+\s+get\s+\d+\b",
            re.IGNORECASE,
        ),
    ),
    (
        "states_a_date",
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
            rf"|\b{_DAY}\s+{_MONTH}(?:\s+\d{{4}})?(?!\w)|\b{_MONTH}\s+{_DAY}\b|\b{_MONTH}\s+\d{{4}}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "states_a_promo_code",
        re.compile(
            r"\b(?:promo|coupon|discount|voucher)\s*code\b|\buse\s+code\b|\bcode\s*:\s*\S",
            re.IGNORECASE,
        ),
    ),  # fmt: skip
    ("contains_an_email_address", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("contains_a_phone_number", re.compile(r"(?<![\w.,])\+?\d(?:[\s-]?\d){9,12}(?![\w.,])")),
)


def content_screens(content: str) -> list[str]:
    """Memory.md §6.2's never-written forms that can be detected in the text itself."""
    return [reason for reason, pattern in _SCREENS if pattern.search(content)]


def _basis(candidate: Candidate, report: GateReport, truth: ProductTruth) -> list[str]:
    if candidate.claim_id is None:
        return ["no_claim_basis"]
    claim = truth.claim(candidate.claim_id)
    if claim is None:
        return ["claim_not_of_this_product"]
    if claim.claim_type == "deduced":
        return ["deduced_without_scored_source"]  # source scoring arrives in Phase 5
    if claim.claim_type != "verified" or claim.retired:
        return ["claim_not_verified_and_active"]
    reasons = []
    if not any(r.claim_id == claim.id and r.label == "verified" for r in report.resolutions):
        reasons.append("claim_not_resolved_by_the_gate")
    if any(claim.id in f.refs for f in report.findings):
        reasons.append("gate_finding_on_this_claim")
    return reasons


async def stage_writeback(
    stage: MemoryStage,
    candidates: Sequence[Candidate],
    *,
    run_id: UUID,
    org_id: UUID,
    brand_id: UUID,
    product_id: UUID,
    report: GateReport,
    truth: ProductTruth,
    encoder: WritebackEncoder,
    cfg: Writeback | None = None,
) -> Screening:
    """Screen the run's candidates; embed and stage the eligible ones. Nothing is written here."""
    cfg = cfg or load_scoring().writeback
    eligible: list[tuple[Candidate, str]] = []
    refused: list[Refusal] = []
    for c in candidates:
        content = " ".join(c.content.split())
        reasons: list[str] = []
        if not content:
            reasons.append("empty")
        if report.outcome == "block":
            reasons.append("gate_blocked_the_output")
        reasons += _basis(c, report, truth)
        reasons += content_screens(content)
        if content and encoder.count_tokens(content) > cfg.max_item_tokens:
            reasons.append("longer_than_one_item")
        if reasons:
            refused.append(Refusal(content, tuple(reasons)))
        else:
            eligible.append((c, content))

    vectors = encoder.embed_documents([content for _, content in eligible])
    staged: list[MemoryWrite] = []
    for (c, content), vector in zip(eligible, vectors, strict=True):
        claim = truth.claim(c.claim_id) if c.claim_id else None
        item = MemoryWrite(
            org_id=org_id,
            kind=c.kind,
            content=content,
            provenance=f"run:{run_id}",
            embedding=tuple(vector),
            embedding_model=encoder.model,
            embedding_version=encoder.version,
            brand_id=brand_id,
            product_id=product_id,
            claim_id=UUID(claim.id) if claim else None,
            sector=truth.sector.id if truth.sector else None,
            source_url=claim.evidence_url if claim else None,
            confidence=c.confidence,
            freshness_days=c.freshness_days,
        )
        stage.stage(item)
        staged.append(item)
    log.info(
        "memory write-back screened",
        extra={
            "run_id": str(run_id),
            "staged": len(staged),
            "refused": [{"content": r.content[:80], "reasons": list(r.reasons)} for r in refused],
        },
    )
    return Screening(tuple(staged), tuple(refused))


# ── Commit ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Committed:
    inserted: list[str] = field(default_factory=list)
    reinforced: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)


def vector_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


_NEAREST_SQL = """
select id, revoked, 1 - (embedding operator(public.<=>) %(e)s::public.vector) as cosine
from public.memory_items
where org_id = %(org)s and brand_id is not distinct from %(brand)s and kind = %(kind)s
  and embedding_model = %(model)s and embedding_version = %(version)s and not is_seed
  and 1 - (embedding operator(public.<=>) %(e)s::public.vector) > %(threshold)s
order by revoked desc, embedding operator(public.<=>) %(e)s::public.vector, id
limit 1
"""

_INSERT_SQL = """
insert into public.memory_items
  (org_id, brand_id, product_id, claim_id, kind, content, embedding, embedding_model,
   embedding_version, sector, source_url, provenance, confidence, freshness_days, approved)
values (%(org_id)s, %(brand_id)s, %(product_id)s, %(claim_id)s, %(kind)s, %(content)s,
        %(embedding)s::public.vector, %(embedding_model)s, %(embedding_version)s, %(sector)s,
        %(source_url)s, %(provenance)s, %(confidence)s, %(freshness_days)s, true)
returning id
"""

_REINFORCE_SQL = """
update public.memory_items
set last_used_at = now(),
    -- least() ignores nulls: unguarded, a null confidence would jump to the cap.
    confidence = case when confidence is not null then least(%(cap)s, confidence + %(step)s) end
where id = %(id)s
returning confidence
"""


async def commit_memory(
    conn: AsyncConnection,
    run_id: UUID,
    items: Sequence[MemoryWrite],
    cfg: Writeback | None = None,
) -> Committed:
    """Write the staged items, inside the caller's terminal transaction."""
    cfg = cfg or load_scoring().writeback
    cur = await conn.execute("select org_id from public.runs where id = %s", (run_id,))
    row = await cur.fetchone()
    if row is None:
        raise WritebackRefused(f"run {run_id} not found")
    org_id: UUID = row[0]
    cur = await conn.execute(
        "select id from public.campaigns where run_id = %s and org_id = %s "
        "and status = 'approved' order by approved_at, id limit 1 for share",
        (run_id, org_id),
    )
    campaign = await cur.fetchone()
    if campaign is None:
        raise WritebackRefused(
            f"run {run_id} has no approved campaign: memory is written only from a campaign a "
            "human approved (Memory.md §6.1)"
        )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended('memory_items:' || %s::text, 0))",
        (org_id,),
    )

    done = Committed()
    touched: set[UUID] = set()
    for item in items:
        if item.org_id != org_id:
            raise WritebackRefused(f"a staged item names org {item.org_id}, the run's is {org_id}")
        if item.claim_id is not None:
            cur = await conn.execute(
                "select 1 from public.product_claims where id = %s and org_id = %s "
                "and product_id is not distinct from %s and claim_type = 'verified' "
                "and retired_at is null",
                (item.claim_id, org_id, item.product_id),
            )
            if await cur.fetchone() is None:
                done.skipped.append({"content": item.content, "reason": "claim_no_longer_verified"})
                continue
        embedding = vector_literal(item.embedding)
        cur = await conn.execute(
            _NEAREST_SQL,
            {
                "e": embedding,
                "org": org_id,
                "brand": item.brand_id,
                "kind": item.kind,
                "model": item.embedding_model,
                "version": item.embedding_version,
                "threshold": cfg.near_duplicate_cosine,
            },
        )
        nearest = await cur.fetchone()
        if nearest is not None:
            near_id, revoked, cosine = nearest
            entry: dict[str, object] = {
                "content": item.content,
                "near": str(near_id),
                "cosine": round(float(cosine), 4),
            }
            if revoked:
                done.skipped.append(entry | {"reason": "near_duplicate_of_a_revoked_item"})
            elif near_id in touched:
                done.skipped.append(entry | {"reason": "duplicate_within_this_writeback"})
            else:
                cur = await conn.execute(
                    _REINFORCE_SQL,
                    {"id": near_id, "cap": cfg.confidence_cap, "step": cfg.reuse_confidence_step},
                )
                updated = await cur.fetchone()
                if updated is None:
                    raise RuntimeError(f"memory item {near_id} vanished inside the write-back")
                touched.add(near_id)
                confidence = None if updated[0] is None else float(updated[0])
                done.reinforced.append(entry | {"confidence": confidence})
            continue
        cur = await conn.execute(
            _INSERT_SQL,
            {
                "org_id": item.org_id,
                "brand_id": item.brand_id,
                "product_id": item.product_id,
                "claim_id": item.claim_id,
                "kind": item.kind,
                "content": item.content,
                "embedding": embedding,
                "embedding_model": item.embedding_model,
                "embedding_version": item.embedding_version,
                "sector": item.sector,
                "source_url": item.source_url,
                "provenance": item.provenance,
                "confidence": item.confidence,
                "freshness_days": item.freshness_days,
            },
        )
        inserted = await cur.fetchone()
        if inserted is None:
            raise RuntimeError("insert into memory_items returned no id")
        touched.add(inserted[0])
        done.inserted.append(str(inserted[0]))

    details = {
        "campaign_id": str(campaign[0]),
        "near_duplicate_cosine": cfg.near_duplicate_cosine,
        "inserted": done.inserted,
        "reinforced": done.reinforced,
        "skipped": done.skipped,
    }
    await conn.execute(
        "insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, "
        "entity_id, details) values (%s, 'service', 'write_memory', 'memory.writeback', 'run', "
        "%s, %s)",
        (org_id, str(run_id), json.dumps(details)),
    )
    log.info("memory write-back committed", extra={"run_id": str(run_id), **details})
    return done
