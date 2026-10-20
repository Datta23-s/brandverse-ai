"""Memory write-back, step 15 (Memory.md §6): what may be written, and how it lands.

A run's memory is staged in this process and committed only in the transaction that writes the
run's terminal status (app/core/budgeted_run.py; docs/evaluation-plan.md §2). **Approval gates
what adds trust, never what reduces it** (Memory.md §6, decided 2026-10-19). Additions — new
items, reuse bumps, near-duplicate reinforcement — commit only for a `completed` run with a
human-approved campaign. Corrections — §6.3's decrements and the revocations by decay below the
threshold — commit at every ending that is not an infrastructure failure, `failed` included: a run
that ends `failed` because its additions were refused is still the primary attempt, and its
corrections commit. Write-back has two halves to match:

1. **Screening, when staging** (`stage_writeback`). Each candidate is checked against what the run
   knows — its gate report and its product's truth — and against Memory.md §6.2. A candidate that
   fails is refused with its reasons and never staged, and never silently: each refusal is logged
   (WARNING, bound to the run) and the step-15 node records them in `audit_log`
   (`record_refusals`), with the content withheld for a candidate refused as personal data. The
   rest are embedded as documents in one batch and staged with the encoder's id and revision
   (Memory.md §5). The stored content is exactly the text embedded: whitespace collapsed, case
   kept (the pinned model's tokenizer lowercases, and so does the lexical index; Memory.md §5).
2. **Commit, inside the terminal transaction** (`commit_memory`, called by `end_run`). What can
   change between staging and the end is checked against the database there: when anything adds
   trust, the run's campaign is approved by a human (§6.1 rule 1; the row is locked for share
   until commit), and each backing claim is still verified and active. Corrections apply first
   (§6.3: confidence − step; below the threshold, revoked by rule with the decay record migration
   0024 requires), then reuse bumps, then new items with their near-duplicates (§6.1 rule 4).

**Eligibility as built (Memory.md §6.1, §6.2):**

- *Campaign approved by a human* (commit, when anything adds trust): the run has a campaign with
  `status = 'approved'`; else the commit is refused, the transaction rolls back and the run ends
  `failed` — and its corrections then commit with that status (app/core/budgeted_run.py).
- *Passed the claim gate* (staging): the candidate rests on a claim the run's gate report resolved
  as `verified`, with no finding against that claim (so a V4 `unrepeated_quantity` flag keeps it
  out), in a report that did not block.
- *`deduced` with a scored source*: refused — no source is scored until Phase 5, so no deduced
  candidate qualifies yet.
- *Generalises* (staging, partly): refused if it states a price, a concrete discount, an explicit
  date or a promo code (§6.2's ephemera); campaign-specific in any other way is NOT detected.
- *Not consumer PII* (staging, partly): refused if it contains an email address or a
  phone-number-shaped digit run; other PII is NOT detected.
- *One fact* (staging): refused above `max_item_tokens`, never split (Memory.md §5, decided
  2026-10-19): half a fact could be retrieved and cited as if it were whole. The author is to
  split the content into separate facts upstream, each a candidate of its own. This also keeps a
  raw page body out (§6.2).
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
  `confidence = min(cap, confidence + step)` (§6.3's numbers; confidence is never null, 0025).

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
from decimal import Decimal
from typing import Literal, Protocol, get_args
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import FindingKind, GateReport
from app.core.logging import bind_run_id
from app.core.truth import ProductTruth
from app.memory.embeddings import Encoder
from app.memory.scoring import Bands, Writeback, load_scoring

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
    confidence: float  # required, as in the table (0025): an item with none could never decay
    brand_id: UUID | None = None
    product_id: UUID | None = None
    claim_id: UUID | None = None
    sector: str | None = None
    source_url: str | None = None
    freshness_days: int | None = None


@dataclass(frozen=True, slots=True)
class Reuse:
    """A retrieved item that appears in the run's campaign, retrieved at `m` (Memory.md §6.3)."""

    item_id: UUID
    m: float


@dataclass(frozen=True, slots=True)
class Correction:
    """Memory.md §6.3's decrement: an item flagged by the validation engine, or edited out by a
    human reviewer. `detail` is the finding kind, or the reviewer's user id; with the run, it is
    what a decay revocation's reason records (migration 0024)."""

    item_id: UUID
    cause: Literal["validation_flag", "reviewer_edit"]
    detail: str

    def __post_init__(self) -> None:
        # Checked here, not first at commit: 0024's constraint would refuse a malformed reason
        # only when the correction happens to revoke, and the whole terminal transaction with it.
        if self.cause == "validation_flag" and self.detail not in get_args(FindingKind):
            raise ValueError(f"not a finding kind: {self.detail!r}")
        if self.cause == "reviewer_edit" and str(UUID(self.detail)) != self.detail:
            raise ValueError(f"not a user id: {self.detail!r}")

    @classmethod
    def flagged(cls, item_id: UUID, finding: FindingKind) -> "Correction":
        return cls(item_id, "validation_flag", finding)

    @classmethod
    def edited_out(cls, item_id: UUID, reviewer: UUID) -> "Correction":
        return cls(item_id, "reviewer_edit", str(reviewer))


@dataclass(frozen=True, slots=True)
class StagedMemory:
    """What a run's end hands the commit. Additions — new items and reuse bumps — add or reinforce
    trust; corrections reduce it. The two are committed under different rules (module docstring)."""

    items: tuple[MemoryWrite, ...] = ()
    reuses: tuple[Reuse, ...] = ()
    corrections: tuple[Correction, ...] = ()

    @property
    def adds_trust(self) -> bool:
        return bool(self.items or self.reuses)

    def __bool__(self) -> bool:
        return self.adds_trust or bool(self.corrections)


class MemoryStage:
    """The run's staged memory writes: in this process only, committed by `end_run` or lost."""

    def __init__(self) -> None:
        self._items: list[MemoryWrite] = []
        self._reuses: list[Reuse] = []
        self._corrections: list[Correction] = []

    def stage(self, item: MemoryWrite) -> None:
        self._items.append(item)

    def reuse(self, reuse: Reuse) -> None:
        self._reuses.append(reuse)

    def correct(self, correction: Correction) -> None:
        """Called where the correction is observed: the validation step (11) or the reviewer's
        edit at approval (12)."""
        self._corrections.append(correction)

    @property
    def items(self) -> tuple[MemoryWrite, ...]:
        return tuple(self._items)

    def all(self) -> StagedMemory:
        """For a completed run: everything staged."""
        return StagedMemory(tuple(self._items), tuple(self._reuses), tuple(self._corrections))

    def corrections_only(self) -> StagedMemory:
        """For any other ending that is not an infrastructure failure: corrections commit,
        additions are discarded."""
        return StagedMemory(corrections=tuple(self._corrections))


# ── Screening ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fact a run proposes to remember, and the claim it rests on (§6.1 rule 2)."""

    kind: MemoryKind
    content: str
    claim_id: str | None
    confidence: float  # stated by whoever proposes the fact; there is no default (0025)
    freshness_days: int | None = None


_PERSONAL = frozenset({"contains_an_email_address", "contains_a_phone_number"})


@dataclass(frozen=True, slots=True)
class Refusal:
    """A candidate that will not be remembered, and why. Never silent: each one is logged when
    screened and recorded in `audit_log` by the step-15 node (`record_refusals`)."""

    kind: str
    claim_id: str | None
    content: str
    reasons: tuple[str, ...]
    tokens: int  # the encoder's own count, so a length refusal can be checked against the cap

    def record(self) -> dict[str, object]:
        """What a log line or an audit row may carry. A candidate refused as personal data has its
        content withheld — copying it would store the PII the refusal kept out. Content that holds
        PII in a form the screens do not detect is not withheld (Memory.md §6.1, as built)."""
        withheld = not _PERSONAL.isdisjoint(self.reasons)
        return {
            "kind": self.kind,
            "claim_id": self.claim_id,
            "reasons": list(self.reasons),
            "tokens": self.tokens,
            "content": None if withheld else self.content,
            "content_withheld": withheld,
        }


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
        tokens = encoder.count_tokens(content) if content else 0
        if tokens > cfg.max_item_tokens:
            # One fact, never split (Memory.md §5): the author splits it into separate facts.
            reasons.append("longer_than_one_fact")
        if reasons:
            refused.append(Refusal(c.kind, c.claim_id, content, tuple(reasons), tokens))
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
    # Bound here, not passed in `extra`: the handler's filter sets run_id from the binding
    # (app/core/logging.py), so an unbound caller would log these lines with run_id null.
    with bind_run_id(str(run_id)):
        for r in refused:
            log.warning("memory candidate refused", extra={"candidate": r.record()})
        log.info(
            "memory write-back screened", extra={"staged": len(staged), "refused": len(refused)}
        )
    return Screening(tuple(staged), tuple(refused))


async def record_refusals(conn: AsyncConnection, run_id: UUID, refused: Sequence[Refusal]) -> None:
    """One `audit_log` row naming the run and each refused candidate with its reasons, so a fact
    that should have been remembered and was not can be found. Not memory: written when the
    step-15 node runs, whatever the run's ending, and not subject to the attempt rule."""
    if not refused:
        return
    cur = await conn.execute(
        "insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, "
        "entity_id, details) select org_id, 'service', 'write_memory', "
        "'memory.candidates_refused', 'run', %s, %s from public.runs where id = %s",
        (str(run_id), json.dumps({"refused": [r.record() for r in refused]}), run_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"run {run_id} not found: its refused candidates were not recorded")


# ── Commit ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Committed:
    corrected: list[dict[str, object]] = field(default_factory=list)
    reused: list[dict[str, object]] = field(default_factory=list)
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
set last_used_at = now(), confidence = least(%(cap)s, confidence + %(step)s)
where id = %(id)s
returning confidence
"""


_ITEM_SQL = """
select org_id, revoked, confidence from public.memory_items where id = %s for update
"""

_DECREMENT_SQL = """
update public.memory_items
set confidence = %(after)s,
    revoked = %(revoke)s,
    revoked_reason = case when %(revoke)s then %(reason)s end
where id = %(id)s
"""


def decay_reason(
    before: Decimal, after: Decimal, threshold: Decimal, c: Correction, run_id: UUID
) -> str:
    """The revocation record migration 0024 requires of a revocation by rule."""
    return (
        f"confidence_decay: confidence {before:.2f} -> {after:.2f} below {threshold:.2f}; "
        f"cause {c.cause}:{c.detail}; run {run_id}"
    )


async def _item_of(conn: AsyncConnection, item_id: UUID, org_id: UUID) -> tuple[bool, Decimal]:
    """Lock a staged correction's or reuse's item. An item of another organisation, or none at
    all, is a defect upstream: the whole commit is refused, never skipped."""
    row = await (await conn.execute(_ITEM_SQL, (item_id,))).fetchone()
    if row is None or row[0] != org_id:
        raise WritebackRefused(f"memory item {item_id} is not an item of org {org_id}")
    revoked, confidence = row[1], row[2]
    if not isinstance(confidence, Decimal):  # not null since 0025; numeric arrives as Decimal
        raise RuntimeError(f"memory item {item_id} has confidence {confidence!r}")
    return bool(revoked), confidence


async def _correct(
    conn: AsyncConnection,
    run_id: UUID,
    org_id: UUID,
    corrections: Sequence[Correction],
    cfg: Writeback,
    done: Committed,
) -> set[UUID]:
    """Memory.md §6.3: confidence − step; below the threshold, revoked by rule (0024)."""
    corrected: set[UUID] = set()
    step, threshold = Decimal(str(cfg.correction_step)), Decimal(str(cfg.revoke_below))
    for c in corrections:
        revoked, before = await _item_of(conn, c.item_id, org_id)
        entry: dict[str, object] = {"item": str(c.item_id), "cause": c.cause, "detail": c.detail}
        if revoked:
            done.skipped.append(entry | {"reason": "already_revoked"})
            continue
        if c.item_id in corrected:
            # One run is one judgement of an item: its first correction counts; the rest are
            # kept in the audit row.
            done.skipped.append(entry | {"reason": "already_corrected_in_this_run"})
            continue
        corrected.add(c.item_id)
        after = before - step
        revoke = after < threshold
        await conn.execute(
            _DECREMENT_SQL,
            {
                "id": c.item_id,
                "after": after,
                "revoke": revoke,
                "reason": decay_reason(before, after, threshold, c, run_id),
            },
        )
        done.corrected.append(
            entry | {"before": float(before), "after": float(after), "revoked": revoke}
        )
    return corrected


async def _reinforce(conn: AsyncConnection, item_id: UUID, cfg: Writeback) -> float:
    cur = await conn.execute(
        _REINFORCE_SQL,
        {"id": item_id, "cap": cfg.confidence_cap, "step": cfg.reuse_confidence_step},
    )
    updated = await cur.fetchone()
    if updated is None:
        raise RuntimeError(f"memory item {item_id} vanished inside the write-back")
    return float(updated[0])


def _config(cfg: Writeback | None, bands: Bands | None) -> tuple[Writeback, Bands]:
    if cfg is not None and bands is not None:
        return cfg, bands
    scoring = load_scoring()
    return cfg or scoring.writeback, bands or scoring.memory.bands


async def commit_memory(
    conn: AsyncConnection,
    run_id: UUID,
    staged: StagedMemory,
    cfg: Writeback | None = None,
    bands: Bands | None = None,
) -> Committed:
    """Write the staged memory, inside the caller's terminal transaction.

    Corrections first, then reuse bumps, then new items, so an item corrected in this run is
    neither bumped nor reinforced by it. A human-approved campaign is required only when something
    adds trust; corrections alone commit without one."""
    cfg, bands = _config(cfg, bands)
    cur = await conn.execute("select org_id from public.runs where id = %s", (run_id,))
    row = await cur.fetchone()
    if row is None:
        raise WritebackRefused(f"run {run_id} not found")
    org_id: UUID = row[0]
    campaign = None
    if staged.adds_trust:
        cur = await conn.execute(
            "select id from public.campaigns where run_id = %s and org_id = %s "
            "and status = 'approved' order by approved_at, id limit 1 for share",
            (run_id, org_id),
        )
        campaign = await cur.fetchone()
        if campaign is None:
            raise WritebackRefused(
                f"run {run_id} has no approved campaign: memory that adds trust is written only "
                "from a campaign a human approved (Memory.md §6.1)"
            )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended('memory_items:' || %s::text, 0))",
        (org_id,),
    )

    done = Committed()
    corrected = await _correct(conn, run_id, org_id, staged.corrections, cfg, done)
    touched: set[UUID] = set()
    for r in staged.reuses:
        revoked, _ = await _item_of(conn, r.item_id, org_id)
        entry: dict[str, object] = {"item": str(r.item_id), "m": round(r.m, 4)}
        if r.m < bands.reuse:
            done.skipped.append(entry | {"reason": "below_the_reuse_band"})
        elif revoked:
            done.skipped.append(entry | {"reason": "revoked"})
        elif r.item_id in corrected:
            done.skipped.append(entry | {"reason": "corrected_in_this_run"})
        elif r.item_id in touched:
            done.skipped.append(entry | {"reason": "counted_once_in_this_run"})
        else:
            confidence = await _reinforce(conn, r.item_id, cfg)
            touched.add(r.item_id)
            done.reused.append(entry | {"confidence": confidence})

    for item in staged.items:
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
            near: dict[str, object] = {
                "content": item.content,
                "near": str(near_id),
                "cosine": round(float(cosine), 4),
            }
            if revoked:
                done.skipped.append(near | {"reason": "near_duplicate_of_a_revoked_item"})
            elif near_id in corrected:
                done.skipped.append(near | {"reason": "near_duplicate_corrected_in_this_run"})
            elif near_id in touched:
                done.skipped.append(near | {"reason": "duplicate_within_this_writeback"})
            else:
                confidence = await _reinforce(conn, near_id, cfg)
                touched.add(near_id)
                done.reinforced.append(near | {"confidence": confidence})
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
        "campaign_id": str(campaign[0]) if campaign else None,
        "near_duplicate_cosine": cfg.near_duplicate_cosine,
        "corrected": done.corrected,
        "reused": done.reused,
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
    with bind_run_id(str(run_id)):
        log.info("memory write-back committed", extra=details)
    return done


# ── Step 15 ───────────────────────────────────────────────────────────────────────────────────


async def write_memory(
    pool: AsyncConnectionPool[AsyncConnection],
    stage: MemoryStage,
    *,
    run_id: UUID,
    org_id: UUID,
    brand_id: UUID,
    product_id: UUID,
    approved: bool,
    candidates: Sequence[Candidate],
    reused: Sequence[Reuse],
    report: GateReport,
    truth: ProductTruth,
    encoder: WritebackEncoder,
    cfg: Writeback | None = None,
    bands: Bands | None = None,
) -> Screening:
    """The step-15 node (Architecture.md §5.1). It stages; it never writes memory — `end_run`
    commits at the run's end. Corrections are not staged here: steps 11 and 12 stage them where
    they are observed. `approved` is the human decision from step 12; the commit checks it again
    against the database.

    For an unapproved campaign nothing that adds trust is staged, and nothing is recorded as
    refused: §6.2 says nothing from a rejected campaign is written, so no fact is lost. For an
    approved one, candidates are screened and staged, each refusal is recorded in `audit_log`, and
    the campaign's reused items are staged for their bump."""
    if not approved:
        with bind_run_id(str(run_id)):
            log.info(
                "campaign not approved: no memory staged that adds trust",
                extra={"candidates": len(candidates), "reused": len(reused)},
            )
        return Screening((), ())
    cfg, bands = _config(cfg, bands)
    below = [str(r.item_id) for r in reused if r.m < bands.reuse]
    if below:
        # A defect upstream: only an item retrieved at M ≥ the reuse band is bumped (§6.3).
        raise ValueError(f"reuse below the {bands.reuse} band: {below}")
    screening = await stage_writeback(
        stage,
        candidates,
        run_id=run_id,
        org_id=org_id,
        brand_id=brand_id,
        product_id=product_id,
        report=report,
        truth=truth,
        encoder=encoder,
        cfg=cfg,
    )
    for r in reused:
        stage.reuse(r)
    async with pool.connection() as conn:
        await record_refusals(conn, run_id, screening.refused)
    return screening
