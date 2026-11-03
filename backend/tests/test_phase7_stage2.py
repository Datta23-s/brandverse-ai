"""Stage 2's decision logic: both definitions of U from one recall, the separation test, the interim
gate and the refusals that make the registration and the gate impossible to skip. No database, no
encoder, no model call."""

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


def test_both_definitions_come_from_the_same_recall() -> None:
    # 6 relevant items, no slot filled at M >= 0.80: new U 0.5 (D1), old U 1.0 (D1 at its bands)
    c = score(2, [0.6] * 6 + [0.3] * 4, old_cov=0.0)
    assert (c.n_relevant, c.n_recalled) == (6, 10)
    assert c.u_new == pytest.approx(0.5)
    assert c.u_old == 1.0
    assert c.depth_new == "D1"
    assert c.depth_old == "D1"


def test_the_old_definition_cannot_see_quantity() -> None:
    few, many = score(2, [0.6] * 4, 0.0), score(3, [0.6] * 10, 0.0)
    assert few.depth_new == "D2" and many.depth_new == "D0"
    assert few.depth_old == many.depth_old == "D1"  # stage 1's failure, reproduced


def test_sensitivity_is_reported_at_the_other_floors_and_never_changes_the_depth() -> None:
    c = score(2, [0.52] * 8, 0.0)  # passes at 0.50, fails at 0.55 and 0.60
    assert c.n_relevant == 0 and c.depth_new == "D2"
    assert c.depth_new_at_floor == {"0.50": "D1", "0.60": "D2"}


def test_separation_needs_three_different_bands_in_the_registered_order() -> None:
    assert s2.separates(["D2", "D1", "D0"])
    assert not s2.separates(["D2", "D1", "D1"])
    assert not s2.separates(["D2", "D0", "D1"])
    assert not s2.separates(["D1", "D1", "D1"])
    assert not s2.separates(["D2", "D1"])


def test_r_monotonic_is_the_continuous_version() -> None:
    assert s2.r_monotonic([0.78, 0.30, 0.18])
    assert not s2.r_monotonic([0.78, 0.30, 0.27])


def brand_scores(new: tuple[str, str, str], old: tuple[str, str, str]) -> list[s2.CampaignScore]:
    out = []
    for i in range(3):
        c = score(i + 1, [0.6] * 5, 0.0)
        out.append(
            s2.CampaignScore(
                **{**s2.asdict(c), "depth_new": new[i], "depth_old": old[i]}  # type: ignore[arg-type]
            )
        )
    return out


SEP = ("D2", "D1", "D0")
FLAT = ("D2", "D1", "D1")


def interim(
    per_brand: list[tuple[tuple[str, str, str], tuple[str, str, str]]],
) -> s2.InterimVerdict:
    return s2.interim_verdict(
        {b: brand_scores(*pair) for b, pair in zip(s2.INTERIM_BRANDS, per_brand, strict=True)}
    )


def test_the_interim_passes_on_three_of_four_under_either_definition() -> None:
    assert interim([(SEP, FLAT)] * 3 + [(FLAT, FLAT)]).passed  # new carries it
    assert interim([(FLAT, SEP)] * 3 + [(FLAT, FLAT)]).passed  # old carries it
    assert not interim([(SEP, FLAT)] * 2 + [(FLAT, SEP)] * 2).passed  # two each: neither reaches 3


def test_an_incomplete_interim_cannot_pass() -> None:
    three = {b: brand_scores(SEP, SEP) for b in s2.INTERIM_BRANDS[:3]}
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


def test_the_predictions_are_registered_for_both_definitions() -> None:
    assert set(s2.PREDICTIONS) == {"new", "old"}
    assert s2.PREDICTIONS["new"][3][0] == "D0"
    assert s2.PREDICTIONS["old"][3] == ("D1",)  # the old definition is predicted NOT to separate
