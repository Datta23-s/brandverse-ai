"""Run admission, pipeline steps 2–3 (app/core/admission.py; PRD.md §5; Phases.md Phase 3).

Pure decisions: every input a check reads is given here, so each rule is asserted on its own and
in combination. The database-backed path is in tests/db/test_api.py.
"""

from datetime import date

import pytest

from app.core.admission import AdmissionInputs, RestrictedClaim, decide_admission
from app.core.sector_registry import Sector, SectorConstraint

TODAY = date(2026, 10, 17)
FOOTWEAR = Sector("footwear", regulated=False)
SUPPLEMENTS = Sector("health-supplements", regulated=True)


def _rule(
    rule_id: str = "r1",
    sector: str = "health-supplements",
    kind: str = "prohibited_phrase",
    pattern: str | None = "cures",
    match_type: str = "phrase",
    retired: date | None = None,
) -> SectorConstraint:
    return SectorConstraint(
        rule_id,
        sector,
        kind,
        date(2026, 1, 1),
        retired,
        match_type,
        pattern,  # type: ignore[arg-type]
    )


def _inputs(
    sector: Sector | None = FOOTWEAR,
    verified: int = 1,
    restricted: tuple[RestrictedClaim, ...] = (),
    rules: tuple[SectorConstraint, ...] = (),
) -> AdmissionInputs:
    return AdmissionInputs(sector, verified, restricted, rules)


def codes(inputs: AdmissionInputs, brief: str | None = None) -> list[str]:
    return [r.code for r in decide_admission(inputs, brief, TODAY).reasons]


def test_a_product_with_a_verified_claim_in_a_covered_sector_is_admitted() -> None:
    decision = decide_admission(_inputs(), "Launch the new trail shoe", TODAY)
    assert not decision.blocked
    assert decision.reasons == ()


@pytest.mark.parametrize("verified", [0, -1])
def test_a_product_with_no_verified_claim_is_blocked(verified: int) -> None:
    assert codes(_inputs(verified=verified)) == ["no_verified_claims"]


def test_a_brand_with_no_sector_is_blocked() -> None:
    assert codes(_inputs(sector=None)) == ["registry_coverage"]


def test_a_regulated_sector_with_no_active_rules_is_blocked() -> None:
    assert codes(_inputs(sector=SUPPLEMENTS)) == ["registry_coverage"]
    retired = _rule(retired=TODAY)
    assert codes(_inputs(sector=SUPPLEMENTS, rules=(retired,))) == ["registry_coverage"]


def test_a_regulated_sector_with_an_active_rule_is_admitted() -> None:
    decision = decide_admission(_inputs(sector=SUPPLEMENTS, rules=(_rule(),)), None, TODAY)
    assert not decision.blocked
    assert decision.active_rule_ids == ("r1",)


def test_a_brief_requesting_a_restricted_claim_is_blocked_and_names_it() -> None:
    restricted = (RestrictedClaim("c9", "Unbeatable signature sound"),)
    decision = decide_admission(
        _inputs(restricted=restricted), "Lead with its UNBEATABLE signature-sound!", TODAY
    )
    assert [(r.code, r.refs) for r in decision.reasons] == [("restricted_claim_requested", ("c9",))]


def test_a_paraphrase_of_a_restricted_claim_is_not_caught_here() -> None:
    # Stated, not hidden: deterministic matching misses paraphrase (evaluation-plan.md, V2).
    restricted = (RestrictedClaim("c9", "Unbeatable signature sound"),)
    assert codes(_inputs(restricted=restricted), "Say nothing beats how it sounds") == []


def test_a_brief_using_prohibited_wording_for_the_sector_is_blocked() -> None:
    inputs = _inputs(sector=SUPPLEMENTS, rules=(_rule(),))
    decision = decide_admission(inputs, "Tell them it cures fatigue", TODAY)
    assert [(r.code, r.refs) for r in decision.reasons] == [
        ("prohibited_phrase_requested", ("r1",))
    ]


def test_regex_rules_apply_to_the_brief() -> None:
    rule = _rule(pattern=r"\b100\s*%\s*natural\b", match_type="regex")
    assert codes(_inputs(sector=SUPPLEMENTS, rules=(rule,)), "It is 100 % Natural") == [
        "prohibited_phrase_requested"
    ]


def test_rules_of_other_sectors_and_retired_rules_do_not_apply_to_the_brief() -> None:
    other = _rule("r2", sector="footwear")
    retired = _rule("r3", retired=date(2026, 6, 1))
    active = _rule("r4", pattern="unrelated")
    inputs = _inputs(sector=SUPPLEMENTS, rules=(other, retired, active))
    assert codes(inputs, "It cures fatigue") == []


def test_disclaimer_rules_never_block_a_brief() -> None:
    disclaimer = _rule(kind="required_disclaimer", pattern=None)
    assert codes(_inputs(sector=SUPPLEMENTS, rules=(disclaimer,)), "anything") == []


def test_an_invalid_registry_rule_blocks_rather_than_passing() -> None:
    broken = _rule(pattern="(unclosed", match_type="regex")
    assert codes(_inputs(sector=SUPPLEMENTS, rules=(broken,)), "any brief") == [
        "registry_rule_invalid"
    ]


def test_every_failing_check_is_reported_not_only_the_first() -> None:
    restricted = (RestrictedClaim("c9", "clinically proven"),)
    inputs = _inputs(sector=SUPPLEMENTS, verified=0, restricted=restricted)
    assert codes(inputs, "clinically proven formula") == [
        "no_verified_claims",
        "registry_coverage",
        "restricted_claim_requested",
    ]


@pytest.mark.parametrize("brief", [None, ""])
def test_no_brief_means_nothing_to_scan(brief: str | None) -> None:
    restricted = (RestrictedClaim("c9", "clinically proven"),)
    assert codes(_inputs(restricted=restricted), brief) == []


def test_every_message_naming_a_sector_states_its_rules_provenance() -> None:
    illustrative = Sector("health-supplements", regulated=True)
    authoritative = Sector("x", regulated=True, rules_provenance="authoritative")
    assert (
        "illustrative rules: synthetic, not derived from any regulation" in illustrative.describe()
    )
    assert "authoritative rules" in authoritative.describe()
    coverage = decide_admission(_inputs(sector=illustrative), None, TODAY).reasons[0].message
    assert "illustrative rules" in coverage
    banned = (
        decide_admission(_inputs(sector=illustrative, rules=(_rule(),)), "it cures fatigue", TODAY)
        .reasons[0]
        .message
    )
    assert "illustrative rules" in banned
