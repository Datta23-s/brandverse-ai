"""The registered rating subset (decision-log 2.60, 2.62): stratified, brand-balanced, frozen.

The file is the committed copy of a draw the code reproduces. These tests stop the two drifting,
reproduce every candidate the registration records, and pin the frozen draw's shape so a quiet
re-draw fails a test rather than moving a figure.
"""

import hashlib

import pytest
import yaml

from app.evaluation.rating_subset import (
    PER_POSITION,
    REGISTERED,
    SEED,
    SIZE,
    campaigns_in_subset,
    draw,
    rating_subset,
    registered_file,
    subset_sha256,
)
from app.evaluation.schedule import ARMS, EXCLUDED_BRANDS, TEST_SET
from app.evaluation.schedule import test_set_requests as request_rows

REGISTRATION = registered_file()
TEST_SET_DATA = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))
BRAND_OF = {r["id"]: r["brand"] for r in TEST_SET_DATA["requests"]}
POSITION_OF = {r["id"]: int(r["position"]) for r in TEST_SET_DATA["requests"]}
REGULATED = {b["id"] for b in TEST_SET_DATA["brands"] if b["regulated"]}


def test_the_committed_subset_is_exactly_what_the_code_draws() -> None:
    assert REGISTRATION["version"] == 2
    assert REGISTRATION["seed"] == SEED == 2026111102
    assert REGISTRATION["size"] == SIZE == 15
    assert REGISTRATION["per_position"] == PER_POSITION == 5
    assert REGISTRATION["requests"] == rating_subset()
    assert REGISTRATION["sha256_of_requests"] == subset_sha256()
    assert REGISTRATION["registered"].isoformat() == REGISTERED == "2026-11-11"
    assert REGISTRATION["eligible_requests"] == len(request_rows()) == 24
    assert REGISTRATION["campaigns_per_rater"] == campaigns_in_subset() == 45 == 15 * len(ARMS)


def test_the_registration_declares_the_design_rather_than_a_plain_draw() -> None:
    """The owner's requirement: the registration must SAY it is stratified and brand-balanced."""
    assert REGISTRATION["stratified"] is True
    assert REGISTRATION["brand_balanced"] is True
    assert REGISTRATION["regulated_included_by_construction"] == ["R19", "R20", "R21"]


# ── the three constraints, each asserted as a property of the frozen draw ─────────────────────


def test_constraint_1_five_requests_at_each_position() -> None:
    positions = [POSITION_OF[r] for r in rating_subset()]
    assert [positions.count(n) for n in (1, 2, 3)] == [5, 5, 5]
    assert REGISTRATION["shape"]["by_position"] == {1: 5, 2: 5, 3: 5}


def test_constraint_2_the_regulated_brands_request_at_every_position_is_in() -> None:
    chosen = rating_subset()
    regulated = sorted(r for r in chosen if BRAND_OF[r] in REGULATED)
    assert regulated == ["R19", "R20", "R21"]
    assert sorted(POSITION_OF[r] for r in regulated) == [1, 2, 3]


def test_constraint_3_every_brand_in_the_evaluation_is_represented() -> None:
    brands = {BRAND_OF[r] for r in rating_subset()}
    runnable_brands = {BRAND_OF[r["id"]] for r in request_rows()}
    assert brands == runnable_brands
    assert len(brands) == 8
    assert REGISTRATION["shape"]["brands_with_none"] == []
    assert not any(BRAND_OF[r] in EXCLUDED_BRANDS for r in rating_subset())


# ── reproducibility, from the seed and the stated procedure alone ─────────────────────────────


def test_the_draw_is_recomputable_by_hand_from_the_seed_and_the_procedure() -> None:
    """The registration's procedure, written out again independently of the module's own loop: per
    position, the regulated request first, then uncovered brands in hash order, then the rest."""
    rows = [
        {
            "id": r["id"],
            "brand": r["brand"],
            "position": int(r["position"]),
            "regulated": r["brand"] in REGULATED,
        }
        for r in TEST_SET_DATA["requests"]
        if r["id"] in {x["id"] for x in request_rows()}
    ]
    covered: set[str] = set()
    by_hand: list[str] = []
    for position in (1, 2, 3):
        at_position = sorted(
            (r for r in rows if r["position"] == position),
            key=lambda r: hashlib.sha256(f"{SEED}|rating:{r['id']}".encode()).hexdigest(),
        )
        take = [r for r in at_position if r["regulated"]]
        covered |= {r["brand"] for r in take}
        for uncovered_only in (True, False):
            for row in at_position:
                if len(take) >= 5 or row in take:
                    continue
                if uncovered_only and row["brand"] in covered:
                    continue
                take.append(row)
                covered.add(row["brand"])
        by_hand += [r["id"] for r in take]
    assert sorted(by_hand) == rating_subset()


def test_the_draw_depends_on_the_seed_so_the_seed_is_what_is_registered() -> None:
    assert draw(seed=SEED) == rating_subset()
    assert draw(seed=SEED + 1) != rating_subset()


# ── every candidate the registration records is reproducible and distinct ─────────────────────


def test_the_superseded_and_unadopted_draws_are_reproducible_from_the_record() -> None:
    """A reader must be able to see every selection considered AND check it. Draws 2 and 3 share a
    seed and differ only by constraint 3, which is why the constraints are registered."""
    candidates = {c["draw"]: c for c in REGISTRATION["candidate_draws"]}
    assert set(candidates) == {1, 2, 3}

    plain = draw(seed=candidates[2]["seed"], prefer_uncovered_brand=False)
    assert plain == candidates[2]["requests"]
    assert hashlib.sha256("\n".join(plain).encode()).hexdigest() == candidates[2]["sha256"]
    assert {BRAND_OF[r] for r in plain} | set(candidates[2]["brands_with_none"]) == {
        BRAND_OF[r["id"]] for r in request_rows()
    }, "draw 2's recorded defect is that B05 and B08 are absent"

    # draw 1 was unstratified; its requests and hash are recorded, and the hash must match them
    first = candidates[1]["requests"]
    assert len(first) == 15
    assert hashlib.sha256("\n".join(first).encode()).hexdigest() == candidates[1]["sha256"]
    positions = [POSITION_OF[r] for r in first]
    assert [positions.count(n) for n in (1, 2, 3)] == [4, 7, 4], candidates[1]["defect"]

    assert candidates[3]["sha256"] == subset_sha256()
    assert candidates[1]["status"] == "SUPERSEDED"
    assert candidates[2]["status"] == "NOT ADOPTED"
    assert candidates[3]["status"].startswith("FROZEN")
    assert all(c.get("defect") or c.get("reason") for c in candidates.values())


def test_the_three_candidates_are_three_different_selections() -> None:
    shas = {c["sha256"] for c in REGISTRATION["candidate_draws"]}
    assert len(shas) == 3


# ── the mechanics ─────────────────────────────────────────────────────────────────────────────


def test_a_draw_is_a_subset_in_request_order_with_no_repeats() -> None:
    chosen = rating_subset()
    assert chosen == sorted(chosen) == sorted(set(chosen))
    assert set(chosen) <= {r["id"] for r in request_rows()}


@pytest.mark.parametrize("per_position", [1, 3, 8])
def test_the_per_position_count_is_what_it_says(per_position: int) -> None:
    chosen = draw(per_position=per_position)
    assert len(chosen) == 3 * per_position
    positions = [POSITION_OF[r] for r in chosen]
    assert [positions.count(n) for n in (1, 2, 3)] == [per_position] * 3
