"""Write-back screening, the half that runs when a candidate is staged (app/memory/writeback.py;
Memory.md §6.1, §6.2).

Pure: the gate report and the product's truth are built here, and the encoder is the hashing
stand-in. The commit half — approval, near-duplicates, the audit row — is in
tests/db/test_writeback_commit.py.
"""

import io
import json
import logging
from collections.abc import Iterator, Sequence
from datetime import date
from uuid import UUID, uuid4

import pytest

from app.core.gates import Finding, GateReport, Resolution
from app.core.logging import configure_logging
from app.core.sector_registry import Sector
from app.core.truth import Claim, ProductTruth
from app.memory.embeddings import HashingEncoder
from app.memory.scoring import load_scoring
from app.memory.writeback import Candidate, MemoryStage, Screening, content_screens, stage_writeback

pytestmark = pytest.mark.anyio

RUN, ORG, BRAND, PRODUCT = uuid4(), uuid4(), uuid4(), uuid4()
FOOTWEAR = Sector("footwear", regulated=False)
CFG = load_scoring().writeback.model_copy(update={"max_item_tokens": 40})

VERIFIED = Claim(
    str(uuid4()), "Upper made of recycled mesh", "verified", "https://e.invalid/v", False
)
UNRESOLVED = Claim(str(uuid4()), "Sole is stitched", "verified", "https://e.invalid/u", False)
QUANTITY = Claim(str(uuid4()), "Up to 60 hours playback", "verified", "https://e.invalid/q", False)
DEDUCED = Claim(str(uuid4()), "Suits daily commuting", "deduced", "https://e.invalid/d", False)
RETIRED = Claim(str(uuid4()), "Weighs 240 g", "verified", "https://e.invalid/r", True)
TRUTH = ProductTruth(FOOTWEAR, (VERIFIED, UNRESOLVED, QUANTITY, DEDUCED, RETIRED), ())


class LogLines:
    """The production JSON log lines of app.memory.writeback (as tests/test_logging.py)."""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    def read(self) -> list[dict[str, object]]:
        entries = [json.loads(line) for line in self.stream.getvalue().splitlines()]
        return [e for e in entries if e["logger"] == "app.memory.writeback"]


@pytest.fixture
def lines() -> Iterator[LogLines]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    captured = LogLines()
    configure_logging("INFO", stream=captured.stream)
    yield captured
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _resolved(*claims: Claim) -> tuple[Resolution, ...]:
    return tuple(Resolution(c.text, "verified", c.id, c.evidence_url) for c in claims)


def _report(*findings: Finding) -> GateReport:
    return GateReport(
        findings=findings,
        resolutions=_resolved(VERIFIED, QUANTITY),
        sector="footwear",
        superlatives_version="test",
        checked_on=date(2026, 10, 19),
    )


class CountingEncoder(HashingEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[list[str]] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return super().embed_documents(texts)


async def _screen(
    *candidates: Candidate, report: GateReport | None = None, encoder: CountingEncoder | None = None
) -> tuple[Screening, MemoryStage]:
    stage = MemoryStage()
    screening = await stage_writeback(
        stage,
        candidates,
        run_id=RUN,
        org_id=ORG,
        brand_id=BRAND,
        product_id=PRODUCT,
        report=report or _report(),
        truth=TRUTH,
        encoder=encoder or CountingEncoder(),
        cfg=CFG,
    )
    return screening, stage


def _reasons(screening: Screening) -> list[tuple[str, ...]]:
    return [r.reasons for r in screening.refused]


# ── Staging ──────────────────────────────────────────────────────────────────────────────────


async def test_an_eligible_candidate_is_embedded_and_staged_with_its_provenance() -> None:
    encoder = CountingEncoder()
    screening, stage = await _screen(
        Candidate(
            "insight", "  Recycled-mesh uppers   lead the\nfootwear copy  ", VERIFIED.id, 0.7
        ),
        encoder=encoder,
    )
    assert screening.refused == ()
    assert stage.items == screening.staged
    [item] = stage.items
    # The stored content is exactly the text embedded: whitespace collapsed, case kept.
    assert item.content == "Recycled-mesh uppers lead the footwear copy"
    assert encoder.batches == [[item.content]]
    assert list(item.embedding) == encoder.embed_documents([item.content])[0]
    assert (item.embedding_model, item.embedding_version) == (encoder.model, encoder.version)
    assert item.provenance == f"run:{RUN}"
    assert (item.org_id, item.brand_id, item.product_id) == (ORG, BRAND, PRODUCT)
    assert item.claim_id == UUID(VERIFIED.id)
    assert item.source_url == VERIFIED.evidence_url
    assert item.sector == "footwear"
    assert item.confidence == 0.7


async def test_refused_candidates_are_never_embedded_and_the_rest_go_in_one_batch() -> None:
    encoder = CountingEncoder()
    screening, stage = await _screen(
        Candidate("insight", "Recycled mesh reads as premium", VERIFIED.id, 0.7),
        Candidate("insight", "no basis at all", None, 0.7),
        Candidate("claim", "Playback lasts a full day and more", QUANTITY.id, 0.7),
        encoder=encoder,
    )
    assert len(stage.items) == 2
    assert _reasons(screening) == [("no_claim_basis",)]
    assert encoder.batches == [[i.content for i in stage.items]]


@pytest.mark.parametrize(
    ("claim", "reason"),
    [
        (None, "no_claim_basis"),
        (str(uuid4()), "claim_not_of_this_product"),
        (DEDUCED.id, "deduced_without_scored_source"),  # until Phase 5 scores sources
        (RETIRED.id, "claim_not_verified_and_active"),
        (UNRESOLVED.id, "claim_not_resolved_by_the_gate"),
    ],
    ids=["none", "other_product", "deduced", "retired", "unresolved"],
)
async def test_a_candidate_must_rest_on_a_verified_claim_the_gate_resolved(
    claim: str | None, reason: str
) -> None:
    screening, stage = await _screen(
        Candidate("insight", "Recycled mesh reads as premium", claim, 0.7)
    )
    assert stage.items == ()
    assert _reasons(screening) == [(reason,)]


async def test_a_gate_finding_against_the_claim_keeps_it_out() -> None:
    # V4: a statement that dropped the cited claim's quantity is flagged, not blocked; the flag
    # still keeps the claim's fact out of memory.
    flag = Finding("unrepeated_quantity", "60 hours not repeated", (QUANTITY.id,))
    screening, stage = await _screen(
        Candidate("claim", "Lasts all week", QUANTITY.id, 0.7),
        Candidate("insight", "Recycled mesh reads as premium", VERIFIED.id, 0.7),
        report=_report(flag),
    )
    assert [i.claim_id for i in stage.items] == [UUID(VERIFIED.id)]
    assert _reasons(screening) == [("gate_finding_on_this_claim",)]


async def test_nothing_is_staged_from_a_blocked_output() -> None:
    block = Finding("unsourced_claim", "an unsourced statement elsewhere in the copy")
    screening, stage = await _screen(
        Candidate("insight", "Recycled mesh reads as premium", VERIFIED.id, 0.7),
        report=_report(block),
    )
    assert stage.items == ()
    assert _reasons(screening) == [("gate_blocked_the_output",)]


async def test_one_item_is_one_fact_longer_content_is_refused_not_split() -> None:
    long = " ".join(["recycled mesh"] * 21)  # 42 tokens > max_item_tokens 40
    screening, stage = await _screen(Candidate("insight", long, VERIFIED.id, 0.7))
    assert stage.items == ()
    assert _reasons(screening) == [("longer_than_one_fact",)]
    [refusal] = screening.refused
    assert refusal.tokens == 42  # recorded, so the refusal can be checked against the cap


async def test_every_refusal_is_logged_naming_the_run_and_the_reason(lines: LogLines) -> None:
    # A silently dropped candidate is a memory that should exist and does not, and nobody would
    # find it. The run is named even when the caller did not bind it.
    long = " ".join(["recycled mesh"] * 21)
    await _screen(
        Candidate("insight", long, VERIFIED.id, 0.7),
        Candidate("insight", "Recycled mesh reads as premium", VERIFIED.id, 0.7),
    )
    refused = [e for e in lines.read() if e["msg"] == "memory candidate refused"]
    assert [(e["level"], e["run_id"]) for e in refused] == [("WARNING", str(RUN))]
    assert refused[0]["candidate"] == {
        "kind": "insight",
        "claim_id": VERIFIED.id,
        "reasons": ["longer_than_one_fact"],
        "tokens": 42,
        "content": long,
        "content_withheld": False,
    }


async def test_a_candidate_refused_as_personal_data_is_logged_without_its_content(
    lines: LogLines,
) -> None:
    await _screen(Candidate("insight", "Ask priya.k@example.com for the deck", VERIFIED.id, 0.7))
    [entry] = [e for e in lines.read() if e["msg"] == "memory candidate refused"]
    candidate = entry["candidate"]
    assert isinstance(candidate, dict)
    assert (candidate["content"], candidate["content_withheld"]) == (None, True)
    assert "priya" not in json.dumps(entry)


async def test_an_empty_candidate_is_refused() -> None:
    screening, _ = await _screen(Candidate("insight", " \n\t", VERIFIED.id, 0.7))
    assert _reasons(screening) == [("empty",)]


async def test_every_reason_is_reported_not_only_the_first() -> None:
    screening, _ = await _screen(Candidate("insight", "Flat 500 off, write to a@b.co", None, 0.7))
    assert _reasons(screening) == [
        ("no_claim_basis", "states_a_discount", "contains_an_email_address")
    ]


# ── Memory.md §6.2's never-written forms, as far as the text shows them ────────────────────────


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Now at ₹1,999", "states_a_price"),
        ("Rs. 499 only", "states_a_price"),
        ("just $20", "states_a_price"),
        ("under 2,000 rupees", "states_a_price"),
        ("20% off everything", "states_a_discount"),
        ("flat 500 on the second pair", "states_a_discount"),
        ("BOGO this weekend", "states_a_discount"),
        ("buy 1 get 1", "states_a_discount"),
        ("ends 2026-10-19", "states_a_date"),
        ("ends 19/10/2026", "states_a_date"),
        ("sale on 19th October", "states_a_date"),
        ("from Oct 19", "states_a_date"),
        ("launching October 2026", "states_a_date"),
        ("use code SAVE20", "states_a_promo_code"),
        ("apply the promo code at checkout", "states_a_promo_code"),
        ("code: DIWALI", "states_a_promo_code"),
        ("reach priya.k+shop@example.com", "contains_an_email_address"),
        ("call +91 98765 43210", "contains_a_phone_number"),
        ("call 9876543210", "contains_a_phone_number"),
    ],
)
def test_ephemera_and_pii_in_a_detectable_form_are_caught(text: str, reason: str) -> None:
    assert content_screens(text) == [reason]


@pytest.mark.parametrize(
    "text",
    [
        "Up to 60 hours playback (at 60% volume)",  # a percentage that is not a discount
        "Weighs 240 g",
        "Shoppers may prefer lighter shoes for daily commuting",  # 'may' is not a month here
        "Rated 4.5 by 1,200 reviewers",
        "IPX7 rated against splashes",
        "Founder-story copy outperforms spec-sheet copy",
    ],
)
def test_generalisable_facts_pass_the_screens(text: str) -> None:
    assert content_screens(text) == []
