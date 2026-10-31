"""The deterministic proposer (app/memory/proposer.py; threat V13): which facts a run offers memory.

Before it existed `writeback_step` offered none, so no real run wrote a memory item. These tests pin
what it proposes (A2's quote-verified insights and competitor messages, each with the scored page
behind it), what it does NOT (claims, personas, A1's positioning, whitespace: only item kinds that
can carry the full component set are created), that it is deterministic, and that rule 2 now
accepts "a scored source" for the two kinds that rest on a page and for no other.
"""

import pytest

from app.core.gates import GateReport
from app.core.sector_registry import Sector
from app.core.truth import Claim, ProductTruth
from app.memory.proposer import propose
from app.memory.scoring import load_scoring
from app.memory.writeback import Candidate as WritebackCandidate
from app.memory.writeback import (
    EvidenceSource,
    MemoryStage,
    stage_writeback,
)

CFG = load_scoring()
PAGE_A = "https://reviews.example/best-walking-shoes"
PAGE_B = "https://rival.example/monsoon"


def finding(url: str, score: float = 0.8, **kw: object) -> dict[str, object]:
    return {
        "url": url,
        "title": "t",
        "score": score,
        "components": {"authority": 0.5, "recency": 0.9, "specificity": 0.75},
        "published": "2026-03-14",
        "first_party": False,
    } | kw


def state(
    *facts: dict[str, str], findings: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "agent_outputs": {
            "A2": {"memory_facts": list(facts), "for_copy": ["x"], "whitespace": [{"text": "w"}]}
        },
        "findings": findings if findings is not None else [finding(PAGE_A), finding(PAGE_B)],
    }


INSIGHT = {"kind": "insight", "text": "Sellers make returns easy", "source_url": PAGE_A}
COMPETITOR = {
    "kind": "competitor",
    "text": "rival.example: invites a comparison",
    "source_url": PAGE_B,
}


def test_a2s_verified_facts_become_candidates_with_their_scored_page() -> None:
    p = propose(state(INSIGHT, COMPETITOR), CFG.writeback, CFG.version)
    assert [(c.kind, c.content) for c in p.candidates] == [
        ("insight", "Sellers make returns easy"),
        ("competitor", "rival.example: invites a comparison"),
    ]
    insight = p.candidates[0]
    assert insight.claim_id is None
    assert insight.confidence == CFG.writeback.evidence_item_confidence
    assert insight.freshness_days == 365 and p.candidates[1].freshness_days == 60  # Memory.md 2
    (e,) = insight.evidence
    assert (e.url, e.score, e.first_party, e.config_version) == (PAGE_A, 0.8, False, CFG.version)
    assert p.skipped == ()


def test_the_same_state_gives_the_same_proposal() -> None:
    s = state(INSIGHT, COMPETITOR, INSIGHT)
    assert propose(s, CFG.writeback, CFG.version) == propose(s, CFG.writeback, CFG.version)
    assert len(propose(s, CFG.writeback, CFG.version).candidates) == 2  # the repeat is one fact


def test_whitespace_and_the_copy_lines_are_never_proposed() -> None:
    p = propose(state(), CFG.writeback, CFG.version)  # A2 found no memory fact, only whitespace
    assert p.candidates == () and p.skipped == ()


def test_a_run_with_no_a2_output_proposes_nothing() -> None:
    assert propose({}, CFG.writeback, CFG.version).candidates == ()
    assert propose({"agent_outputs": {"A1": {}}}, CFG.writeback, CFG.version).candidates == ()


def test_a_fact_whose_page_is_not_among_the_findings_is_skipped_and_the_reason_is_kept() -> None:
    p = propose(state(INSIGHT, findings=[finding(PAGE_B)]), CFG.writeback, CFG.version)
    assert p.candidates == ()
    (s,) = p.skipped
    assert s.kind == "insight" and "not among the run's findings" in s.reason


def test_a_finding_made_before_components_were_carried_is_skipped_not_guessed() -> None:
    old = {"url": PAGE_A, "score": 0.8, "title": "t"}
    p = propose(state(INSIGHT, findings=[old]), CFG.writeback, CFG.version)
    assert p.candidates == () and "cannot be recorded" in p.skipped[0].reason


# ── Rule 2 (Memory.md 6.1): a scored source is a basis, for the kinds that rest on a page ────

TRUTH = ProductTruth(
    Sector("footwear", False), (Claim("c1", "Knit upper", "verified", "https://x/", False),), ()
)
REPORT = GateReport((), (), "footwear", "v1", __import__("datetime").date(2026, 10, 31))
EVIDENCE = EvidenceSource(PAGE_A, 0.8, {"authority": 0.5}, "source_scoring/1", "v")


class _Enc:
    model, version = "hash", "1"

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


async def refusal_reasons(c: WritebackCandidate) -> tuple[str, ...]:
    from uuid import uuid4

    screening = await stage_writeback(
        MemoryStage(),
        [c],
        run_id=uuid4(),
        org_id=uuid4(),
        brand_id=uuid4(),
        product_id=uuid4(),
        report=REPORT,
        truth=TRUTH,
        encoder=_Enc(),  # type: ignore[arg-type]
    )
    return screening.refused[0].reasons if screening.refused else ()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insight", "competitor"])
async def test_an_insight_or_competitor_fact_resting_on_a_scored_page_is_eligible(
    kind: str,
) -> None:
    c = WritebackCandidate(kind, "A fact about the category", None, 0.6, evidence=(EVIDENCE,))  # type: ignore[arg-type]
    assert await refusal_reasons(c) == ()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["claim", "persona", "creative", "sector"])
async def test_other_kinds_still_need_a_claim_even_with_a_page(kind: str) -> None:
    c = WritebackCandidate(kind, "A fact", None, 0.6, evidence=(EVIDENCE,))  # type: ignore[arg-type]
    assert "no_claim_basis" in await refusal_reasons(c)


@pytest.mark.anyio
async def test_an_insight_with_no_page_and_no_claim_is_refused() -> None:
    c = WritebackCandidate("insight", "A fact", None, 0.6)
    assert "no_claim_basis" in await refusal_reasons(c)


@pytest.mark.anyio
async def test_a_page_below_the_acceptance_score_is_not_a_basis() -> None:
    weak = EvidenceSource(PAGE_A, 0.1, {"authority": 0.1}, "source_scoring/1", "v")
    c = WritebackCandidate("insight", "A fact", None, 0.6, evidence=(weak,))
    assert "source_below_the_acceptance_score" in await refusal_reasons(c)
