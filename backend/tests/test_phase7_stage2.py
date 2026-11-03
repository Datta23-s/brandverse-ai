"""Stage 2's decision logic: the three U variants from one recall, the separation test, the
registered preference ordering, the interim gate and the refusals that make the registration and
the gate impossible to skip. No database, no encoder, no model call."""

import json
from pathlib import Path

import pytest

from app.memory.scoring import load_scoring
from scripts import phase7_stage2 as s2

CFG = load_scoring().necessity


def score(pos: int, sims: list[float], old_cov: float, novelty: float = 0.21) -> s2.CampaignScore:
    return s2.score_campaign(
        request_id=f"R{pos}",
        position=pos,
        novelty=novelty,
        similarities=sims,
        old_coverage=old_cov,
        slots=(0, 2),
        best_m=0.0,
        regulated=False,
        cfg=CFG,
    )


def test_three_variants_come_from_the_same_recall() -> None:
    # 10 recalled: 6 clear the 0.55 floor. No slot is filled at M >= 0.80.
    c = score(2, [0.6] * 6 + [0.3] * 4, old_cov=0.0)
    assert (c.n_floor, c.n_count, c.n_recalled) == (6, 10, 10)
    assert (c.u_old, c.u_floor, c.u_count) == (1.0, pytest.approx(0.5), pytest.approx(1 / 6))
    assert (c.depth_old, c.depth_floor, c.depth_count) == ("D1", "D1", "D0")


def test_old_cannot_see_quantity_and_count_cannot_fall_as_memory_grows() -> None:
    few, many = score(2, [0.6] * 4, 0.0), score(3, [0.6] * 10, 0.0)
    assert few.depth_floor == "D2" and many.depth_floor == "D0"
    assert few.depth_old == many.depth_old == "D1"  # stage 1's failure, reproduced
    # the construct argument for COUNT: more recalled items never raise U, whatever their similarity
    fewer, more = score(2, [0.3] * 5, 0.0), score(3, [0.3] * 5 + [0.2] * 4, 0.0)
    assert more.u_count < fewer.u_count
    assert more.u_floor == fewer.u_floor == 1.0  # the floor variant sees no growth here


def test_the_count_variant_caps_at_the_injection_cap() -> None:
    assert score(3, [0.1] * 30, 0.0).n_count == 12


def test_sensitivity_is_reported_at_the_other_floors_and_never_changes_the_depth() -> None:
    c = score(2, [0.52] * 8, 0.0)  # passes at 0.50, fails at 0.55 and 0.60
    assert c.n_floor == 0 and c.depth_floor == "D2"
    assert c.depth_floor_at == {"0.50": "D1", "0.60": "D2"}


def test_separation_needs_three_different_bands_in_the_registered_order() -> None:
    assert s2.separates(["D2", "D1", "D0"])
    assert not s2.separates(["D2", "D1", "D1"])
    assert not s2.separates(["D2", "D0", "D1"])
    assert not s2.separates(["D1", "D1", "D1"])
    assert not s2.separates(["D2", "D1"])


def test_r_monotonic_is_the_continuous_version() -> None:
    assert s2.r_monotonic([0.78, 0.30, 0.18])
    assert not s2.r_monotonic([0.78, 0.30, 0.27])


Depths = tuple[str, str, str]
SEP: Depths = ("D2", "D1", "D0")
FLAT: Depths = ("D2", "D1", "D1")


def brand_scores(old: Depths, floor: Depths, count: Depths) -> list[s2.CampaignScore]:
    out = []
    for i in range(3):
        c = score(i + 1, [0.6] * 5, 0.0)
        out.append(
            s2.CampaignScore(
                **{  # type: ignore[arg-type]
                    **s2.asdict(c),
                    "depth_old": old[i],
                    "depth_floor": floor[i],
                    "depth_count": count[i],
                }
            )
        )
    return out


def interim(per_brand: list[tuple[Depths, Depths, Depths]]) -> s2.InterimVerdict:
    return s2.interim_verdict(
        {b: brand_scores(*t) for b, t in zip(s2.INTERIM_BRANDS, per_brand, strict=True)}
    )


def test_the_adopted_variant_is_the_first_in_the_registered_order_that_meets_the_criterion() -> (
    None
):
    assert s2.VARIANTS == ("old", "floor", "count")
    # all three meet it: OLD wins, because the original is preferred
    assert interim([(SEP, SEP, SEP)] * 4).adopted == "old"
    # OLD fails, FLOOR and COUNT meet it: FLOOR wins although COUNT separates more brands
    v = interim([(FLAT, SEP, SEP)] * 3 + [(FLAT, FLAT, SEP)])
    assert v.adopted == "floor" and v.separating == {"old": 0, "floor": 3, "count": 4}
    # only COUNT meets it
    assert interim([(FLAT, FLAT, SEP)] * 3 + [(FLAT, FLAT, FLAT)]).adopted == "count"


def test_the_interim_fails_when_no_variant_reaches_three_of_four() -> None:
    v = interim([(SEP, FLAT, FLAT), (SEP, SEP, FLAT), (FLAT, SEP, SEP), (FLAT, FLAT, SEP)])
    assert v.separating == {"old": 2, "floor": 2, "count": 2}
    assert not v.passed and v.adopted is None


def test_an_incomplete_interim_cannot_pass() -> None:
    three = {b: brand_scores(SEP, SEP, SEP) for b in s2.INTERIM_BRANDS[:3]}
    assert not s2.interim_verdict(three).passed


def test_the_brand_sets_are_fixed_and_exclude_the_contaminated_and_the_regulated() -> None:
    assert s2.INTERIM_BRANDS == ("B01", "B02", "B03", "B05")
    assert "B04" not in s2.CLEAN_BRANDS and "B07" not in s2.CLEAN_BRANDS
    assert set(s2.INTERIM_BRANDS) | set(s2.REST_BRANDS) == set(s2.CLEAN_BRANDS)
    assert not set(s2.INTERIM_BRANDS) & set(s2.REST_BRANDS)


def test_rest_refuses_without_a_passing_interim_report(tmp_path: Path) -> None:
    missing = tmp_path / "none.json"
    assert "run --stage interim first" in (s2.rest_refusal(missing) or "")
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"verdict": {"passed": False}}), encoding="utf-8")
    assert "not met" in (s2.rest_refusal(failed) or "")
    passed = tmp_path / "passed.json"
    passed.write_text(json.dumps({"verdict": {"passed": True}}), encoding="utf-8")
    assert s2.rest_refusal(passed) is None


def test_brand_requests_follow_the_campaign_pattern() -> None:
    reqs = s2.brand_requests()
    assert reqs["B01"] == ("R01", "R02", "R03")
    assert reqs["B10"][0] == "R28"


def test_runnable_reports_brands_whose_research_is_not_recorded() -> None:
    ok, missing = s2.runnable(["B01", "B09"], {"R01", "R02", "R25"})
    assert ok == ["B01"] and missing == {"B09": ["R26"]}


def test_a_prediction_is_registered_for_each_variant_and_the_old_one_is_not_a_blind_one() -> None:
    assert set(s2.PREDICTIONS) == set(s2.VARIANTS)
    assert s2.PREDICTIONS["floor"][3][0] == "D0" and s2.PREDICTIONS["count"][3][0] == "D0"
    assert s2.PREDICTIONS["old"][3] == ("D1",)  # held loosely: stage 1's artifact
