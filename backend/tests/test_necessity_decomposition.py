"""Memory versus novelty (decision-log 2.45): the decomposition, its algebra, and the committed
stage-2 reports it is pre-registered against."""

import json
from pathlib import Path

import pytest

from app.evaluation.necessity_decomposition import Bands, attribute, decompose, from_record
from app.memory.necessity import decide
from app.memory.scoring import load_scoring

CFG = load_scoring().necessity
W = CFG.weights
COUNT_BANDS = Bands(CFG.d0_below, CFG.d2_from)
OLD_BANDS = Bands(0.35, 0.70)  # the stage-1 bands, kept as constants in the stage-2 runner
STAGE2 = Path(__file__).resolve().parents[2] / "evaluation" / "stage2"


def test_the_contributions_sum_to_r_and_use_the_registered_weights() -> None:
    d = decompose(W, COUNT_BANDS, novelty=0.20, u=0.50, s_sector=0.0)
    assert d.r == pytest.approx(d.n_part + d.u_part + d.s_part)
    assert d.n_part == pytest.approx(0.30 * 0.20 / 0.70)
    assert d.u_part == pytest.approx(0.25 * 0.50 / 0.70)
    assert d.s_part == 0.0


def test_under_the_frozen_bands_novelty_alone_can_never_take_the_engine_below_d2() -> None:
    """The algebra behind the pre-registered direction: with U held at 1.00 and N = S = 0, R is
    0.25 / 0.70 = 0.357, above the D2 threshold 0.32. So under COUNT (and FLOOR) any reduction
    below D2 is produced by U, that is by memory, and the memory share is 100 percent by
    construction of the bands; the data checks that the logged inputs obey it."""
    floor = decompose(W, COUNT_BANDS, novelty=0.0, u=1.0, s_sector=0.0)
    # If this fails the weights or the bands MOVED: the registered attribution ("accumulated memory
    # is necessary for any depth reduction", evaluation-plan.md, decision-log 2.45) was derived from
    # them and must be RECOMPUTED and re-registered, not carried over.
    assert floor.r == pytest.approx(0.25 / 0.70), "weights changed: recompute the attribution"
    assert floor.r > CFG.d2_from, "bands changed: recompute the attribution claim (2.45)"
    assert floor.depth == floor.counterfactual_depth == "D2"
    for nov in (0.0, 0.15, 0.5, 1.0):
        assert decompose(W, COUNT_BANDS, novelty=nov, u=1.0, s_sector=0.0).depth == "D2"


def test_a_reduction_caused_by_memory_is_attributed_to_memory() -> None:
    d = decompose(W, COUNT_BANDS, novelty=0.15, u=0.0, s_sector=0.0)  # a full memory
    assert (d.depth, d.counterfactual_depth) == ("D0", "D2")
    assert (d.reduction_levels, d.memory_levels, d.novelty_levels) == (2, 2, 0)


def test_old_bands_make_novelty_alone_a_reduction_which_is_why_it_is_the_negative_control() -> None:
    """OLD keeps U at 1.00 (items start at confidence 0.60, no slot ever fills), yet at N = 0.15
    its R of about 0.42 reads D1 under its own bands: a reduction with NO memory behind it."""
    d = decompose(W, OLD_BANDS, novelty=0.15, u=1.0, s_sector=0.0)
    assert (d.depth, d.counterfactual_depth) == ("D1", "D1")
    assert (d.reduction_levels, d.novelty_levels, d.memory_levels) == (1, 1, 0)


def test_attribution_sums_levels_and_gives_none_when_nothing_was_reduced() -> None:
    nothing = attribute([decompose(W, COUNT_BANDS, novelty=1.0, u=1.0, s_sector=0.0)])
    assert nothing.reduced == 0 and nothing.memory_share is None
    mixed = attribute(
        [
            decompose(W, COUNT_BANDS, novelty=0.15, u=0.0, s_sector=0.0),  # memory, 2 levels
            decompose(W, OLD_BANDS, novelty=0.15, u=1.0, s_sector=0.0),  # novelty, 1 level
        ]
    )
    assert (mixed.reduction_levels, mixed.memory_levels, mixed.novelty_levels) == (3, 2, 1)
    assert mixed.memory_share == pytest.approx(2 / 3)


def test_the_engines_logged_record_decomposes_to_the_depth_it_chose() -> None:
    """From the record the engine writes (R inputs already logged): the decomposition reproduces the
    depth the run used, for each of its inputs."""
    for n_recalled in (0, 3, 7, 12):
        rec = decide(
            CFG,
            novelty=0.15,
            similarities=[0.3] * n_recalled,
            regulated=False,
            requested="D1",
            policy="necessity",
        ).record()
        d = from_record(W, COUNT_BANDS, rec)
        assert d.depth == rec["depth"] and d.r == pytest.approx(float(rec["r"]), abs=1e-3)


def test_the_committed_stage2_reports_give_the_preregistered_picture() -> None:
    """Recorded in 2.45: COUNT's reduction is 100 percent memory, OLD's (the negative control) is
    almost entirely novelty, FLOOR has none to attribute."""
    for name, count_levels, old_memory in (
        ("stage2_interim_20261106.json", 9, 1),
        ("stage2_interim_20261106_attempt24.json", 13, 0),
    ):
        report = json.loads((STAGE2 / name).read_text(encoding="utf-8"))

        def attribution(variant: str, bands: Bands, report: dict = report):  # type: ignore[type-arg]
            return attribute(
                decompose(
                    W,
                    bands,
                    novelty=float(c["novelty"]),
                    u=float(c[f"u_{variant}"]),
                    s_sector=0.0,
                )
                for cs in report["brands"].values()
                for c in cs
                if c["position"] > 1
            )

        count = attribution("count", COUNT_BANDS)
        assert count.memory_share == 1.0 and count.reduction_levels == count_levels
        old = attribution("old", OLD_BANDS)
        assert old.memory_levels == old_memory and old.reduced == 8
        assert attribution("floor", COUNT_BANDS).reduction_levels == 0
