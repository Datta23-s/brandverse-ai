"""The Research Necessity Engine's frozen provisional values (registered 1 November 2026, before any
stage-2 call; docs/phase7-first-decision.md "Registration 2"). A change to any figure fails here:
changing one is a decision that needs a decision-log entry and this file changed with it."""

import pytest

from app.memory.scoring import load_scoring, necessity_depth, necessity_r, quantity_coverage

CFG = load_scoring().necessity


def test_the_frozen_values_are_the_registered_ones() -> None:
    assert CFG.relevance_floor == 0.55
    assert CFG.item_cap == 12
    assert CFG.weights == {"N": 0.30, "U": 0.25, "S": 0.15}
    assert (CFG.d0_below, CFG.d2_from) == (0.20, 0.32)
    # and the reuse bands the engine does not change
    bands = load_scoring().memory.bands
    assert (bands.reuse, bands.adapt, bands.support) == (0.90, 0.80, 0.65)


def test_the_depth_bands_are_the_coverage_terciles_at_the_reference_novelty() -> None:
    """The stated derivation, recomputed: R at U = 2/3 and 1/3, N = 0.20, S = 0, to two decimals."""
    assert round(necessity_r(0.20, 2 / 3, 0.0, CFG), 2) == CFG.d2_from
    assert round(necessity_r(0.20, 1 / 3, 0.0, CFG), 2) == CFG.d0_below


def test_coverage_counts_items_past_the_floor_and_saturates_at_the_cap() -> None:
    assert quantity_coverage([], CFG) == (0, 0.0)
    assert quantity_coverage([0.60, 0.549, 0.55, 0.9], CFG) == (3, 0.25)  # 0.55 itself passes
    assert quantity_coverage([0.9] * 30, CFG) == (12, 1.0)  # never above the cap


@pytest.mark.parametrize(
    ("n_items", "novelty", "regulated", "depth"),
    [
        (0, 1.0, False, "D2"),  # campaign 1: nothing in memory
        (4, 0.21, False, "D2"),  # coverage one third: still D2
        (5, 0.21, False, "D1"),
        (8, 0.21, False, "D1"),
        (9, 0.21, False, "D0"),
        (12, 0.16, False, "D0"),
        # A regulated sector's S adds 0.21 to R: never D0 whatever memory holds, D2 up to 10 items
        (5, 0.21, True, "D2"),
        (10, 0.16, True, "D2"),
        (11, 0.16, True, "D1"),
        (12, 0.16, True, "D1"),
    ],
)
def test_depth_as_registered(n_items: int, novelty: float, regulated: bool, depth: str) -> None:
    _, coverage = quantity_coverage([0.9] * n_items, CFG)
    r = necessity_r(novelty, 1 - coverage, 1.0 if regulated else 0.0, CFG)
    assert necessity_depth(r, CFG) == depth
