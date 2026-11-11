"""The registered rating subset (decision-log 2.59): 15 requests, drawn before the data existed.

The file is the committed copy of a draw the code reproduces; these tests are what stop the two
drifting, and what stop a re-draw being slipped in as an edit.
"""

import hashlib

import pytest
import yaml

from app.evaluation.rating_subset import (
    REGISTERED,
    SEED,
    SIZE,
    campaigns_in_subset,
    draw,
    rating_subset,
    registered_file,
    subset_sha256,
)
from app.evaluation.schedule import ARMS, EXCLUDED_BRANDS, TEST_SET, test_set_requests

REGISTRATION = registered_file()


def test_the_committed_subset_is_exactly_what_the_code_draws() -> None:
    assert REGISTRATION["seed"] == SEED == 20261111
    assert REGISTRATION["size"] == SIZE == 15
    assert REGISTRATION["requests"] == rating_subset()
    assert REGISTRATION["sha256_of_requests"] == subset_sha256()
    assert REGISTRATION["registered"].isoformat() == REGISTERED == "2026-11-11"
    assert REGISTRATION["eligible_requests"] == len(test_set_requests()) == 24
    assert REGISTRATION["campaigns_per_rater"] == campaigns_in_subset() == 45 == 15 * len(ARMS)


def test_every_chosen_request_is_in_the_evaluation_and_none_is_an_excluded_brand() -> None:
    rows = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))["requests"]
    brand_of = {r["id"]: r["brand"] for r in rows}
    chosen = rating_subset()
    assert len(chosen) == len(set(chosen)) == 15
    runnable = {r["id"] for r in test_set_requests()}
    assert set(chosen) <= runnable
    assert not any(brand_of[r] in EXCLUDED_BRANDS for r in chosen)


def test_the_draw_depends_on_the_seed_so_the_seed_is_what_is_registered() -> None:
    requests = [r["id"] for r in test_set_requests()]
    assert draw(requests, seed=SEED) == rating_subset()
    assert draw(requests, seed=SEED + 1) != rating_subset()


def test_the_draw_is_recomputable_by_hand_from_the_seed() -> None:
    """No pseudo-random generator and no library version: the method is a hash ordering, so this
    test recomputes it independently of the module's own helper."""
    requests = [r["id"] for r in test_set_requests()]
    by_hand = sorted(
        sorted(requests, key=lambda r: hashlib.sha256(f"{SEED}|rating:{r}".encode()).hexdigest())[
            :15
        ]
    )
    assert by_hand == rating_subset()


def test_what_the_draw_gave_is_pinned_so_a_redraw_cannot_pass_as_an_edit() -> None:
    """The shape is recorded in the registration as an OUTCOME of the seed. Pinning it here means a
    quiet re-draw fails a test rather than changing a figure."""
    rows = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))["requests"]
    brand_of = {r["id"]: r["brand"] for r in rows}
    position_of = {r["id"]: r["position"] for r in rows}
    regulated = {
        b["id"]
        for b in yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))["brands"]
        if b["regulated"]
    }
    chosen = rating_subset()
    brands = {brand_of[r] for r in chosen}
    assert len(brands) == 8, "every brand in the evaluation has at least one request in the subset"
    assert sorted(r for r in chosen if brand_of[r] in regulated) == ["R19", "R20", "R21"]
    positions = [position_of[r] for r in chosen]
    assert [positions.count(n) for n in (1, 2, 3)] == [4, 7, 4]


@pytest.mark.parametrize("size", [0, 1, 24])
def test_a_draw_of_any_size_is_a_subset_in_request_order(size: int) -> None:
    requests = [r["id"] for r in test_set_requests()]
    chosen = draw(requests, size=size)
    assert chosen == sorted(chosen) and len(chosen) == size and set(chosen) <= set(requests)


def test_a_draw_larger_than_the_set_is_an_error_not_a_truncation() -> None:
    with pytest.raises(ValueError, match="cannot draw"):
        draw([r["id"] for r in test_set_requests()], size=25)
