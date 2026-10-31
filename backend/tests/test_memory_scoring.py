"""The M-score, freshness and reuse bands (app/memory/scoring.py; Memory.md §3; Rules.md §6:
every scoring function tested at its boundaries and just inside and outside each band)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.memory.scoring import (
    Candidate,
    RequestContext,
    band,
    freshness,
    load_scoring,
    memory_coverage,
    score,
)

CFG = load_scoring().memory
NOW = datetime(2026, 10, 18, tzinfo=UTC)
CTX = RequestContext(
    product_id="p1", brand_id="b1", product_category="cookware", sector="kitchenware", now=NOW
)


def cand(**kw: object) -> Candidate:
    base: dict[str, object] = {
        "id": "m1", "kind": "insight", "brand_id": "b1", "product_id": "p1",
        "product_category": "cookware", "claim_type": None, "sector": "kitchenware",
        "confidence": 0.8, "created_at": NOW, "freshness_days": None, "similarity": 0.9,
    }  # fmt: skip
    return Candidate(**(base | kw))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("m", "expected"),
    [
        (1.0, "reuse"), (0.90, "reuse"), (0.8999, "adapt"), (0.80, "adapt"), (0.7999, "support"),
        (0.65, "support"), (0.6499, "fresh"), (0.0, "fresh"),
    ],
)  # fmt: skip
def test_bands_at_and_just_under_each_threshold(m: float, expected: str) -> None:
    assert band(m, CFG.bands) == expected


def test_freshness_halves_at_its_freshness_days_and_quarters_at_twice() -> None:
    at = cand(kind="competitor", created_at=NOW - timedelta(days=60))
    twice = cand(kind="competitor", created_at=NOW - timedelta(days=120))
    assert freshness(at, CTX, CFG) == pytest.approx(math.exp(-0.7), abs=1e-9)  # ~0.50
    assert freshness(twice, CTX, CFG) == pytest.approx(math.exp(-1.4), abs=1e-9)  # ~0.25
    assert freshness(cand(created_at=NOW), CTX, CFG) == 1.0


def test_an_item_states_its_own_freshness_days() -> None:
    item = cand(kind="competitor", freshness_days=30, created_at=NOW - timedelta(days=30))
    assert freshness(item, CTX, CFG) == pytest.approx(math.exp(-0.7))


def test_a_verified_claim_does_not_decay() -> None:
    old = cand(kind="claim", claim_type="verified", created_at=NOW - timedelta(days=3650))
    deduced = cand(kind="claim", claim_type="deduced", created_at=NOW - timedelta(days=365))
    assert freshness(old, CTX, CFG) == 1.0
    assert freshness(deduced, CTX, CFG) == pytest.approx(math.exp(-0.7))


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, 1.0),
        ({"product_id": "p2"}, 0.7),
        ({"product_id": "p2", "brand_id": "b2"}, 0.4),
        ({"product_id": "p2", "brand_id": "b2", "product_category": "linen"}, 0.1),
        ({"product_id": None, "brand_id": None, "product_category": None}, 0.1),
    ],
)
def test_product_match_levels(kw: dict[str, object], expected: float) -> None:
    assert score(cand(**kw), CTX, CFG).components["P"] == expected


def test_sector_alignment_same_unrelated_and_unknown() -> None:
    assert score(cand(), CTX, CFG).components["A"] == 1.0
    assert score(cand(sector="footwear"), CTX, CFG).components["A"] == 0.2
    assert score(cand(sector=None), CTX, CFG).components["A"] == 0.2  # unknown is not rewarded


def test_declared_adjacent_sectors_score_point_six() -> None:
    cfg = CFG.model_copy(update={"adjacent_sectors": [("kitchenware", "home-appliances")]})
    assert score(cand(sector="home-appliances"), CTX, cfg).components["A"] == 0.6


def test_missing_components_are_left_out_and_the_weights_renormalised() -> None:
    s = score(cand(similarity=0.9), CTX, CFG)
    assert s.left_out == ("C", "E")  # no personas (Phase 6), no source score (Phase 5)
    w = CFG.weights
    expected = (w.S * 0.9 + w.P * 1.0 + w.A * 1.0 + w.F * 1.0) / (w.S + w.P + w.A + w.F)
    assert s.m == pytest.approx(expected)


def test_supplied_c_and_e_count() -> None:
    s = score(cand(customer_similarity=0.5, source_score=0.5), CTX, CFG)
    assert s.left_out == ()
    assert s.components["E"] == pytest.approx(0.4)  # 0.5 x confidence 0.8


def test_similarity_is_clamped_not_rescaled() -> None:
    assert score(cand(similarity=-0.4), CTX, CFG).components["S"] == 0.0
    assert score(cand(similarity=0.3), CTX, CFG).components["S"] == 0.3  # not (1 + 0.3) / 2


def test_an_unrelated_old_item_falls_below_the_support_band() -> None:
    far = cand(
        similarity=0.2, product_id="x", brand_id="y", product_category="z", sector="footwear",
        kind="competitor", created_at=NOW - timedelta(days=300),
    )  # fmt: skip
    assert score(far, CTX, CFG).band == "fresh"


def test_memory_coverage_counts_slots_filled_at_the_adapt_band() -> None:
    assert memory_coverage({"persona": [0.82], "competitor": [0.79], "claims": []}, CFG) == 1 / 3
    assert memory_coverage({}, CFG) == 0.0


def test_the_weights_must_sum_to_one() -> None:
    from pydantic import ValidationError

    from app.memory.scoring import Weights

    with pytest.raises(ValidationError, match="sum to 1"):
        Weights(S=0.5, P=0.5, C=0.5, A=0, F=0, E=0)


# ── Coverage is "k of N slots", and N is what memory can contain (2026-10-31) ─────────────────


def test_memory_slots_reports_filled_of_required_never_the_fraction_alone() -> None:
    from app.memory.scoring import memory_slots

    assert memory_slots({"insight": [0.82], "competitor": [0.79]}, CFG) == (1, 2)
    assert memory_slots({"insight": [], "competitor": []}, CFG) == (0, 2)
    assert memory_slots({}, CFG) == (0, 0)


def test_the_required_slots_are_the_kinds_the_system_can_write_with_a_full_component_set() -> None:
    """required_slots was reduced from six to two on 2026-10-31, before any run: only insight and
    competitor items rest on a scored page, so only they can carry E. Coverage over a kind that can
    never be written would be capped by construction (V13 one layer up). This ties the list to
    what the proposer can write, so adding a slot without a way to fill it fails here."""
    from app.memory.scoring import load_scoring
    from app.memory.writeback import EVIDENCE_BASED_KINDS

    slots = load_scoring().retrieval.required_slots
    assert slots == ("insight", "competitor")
    assert set(slots) <= EVIDENCE_BASED_KINDS
