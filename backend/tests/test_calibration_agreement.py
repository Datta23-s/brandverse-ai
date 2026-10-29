"""The calibration figure counts calibration approvals only (migration 0032; rubric section 6).

Tuning approvals (the tuning organisation that measures the strong calls a run takes) are a third
kind and must never be counted as calibration, even by mistake: the query filters on the kind and
the calculation refuses a row of any other kind."""

import pytest

from app.evaluation.approvals import (
    CALIBRATION_APPROVALS_SQL,
    TUNING_APPROVALS_SQL,
    calibration_agreement,
)


def scores(agree: int, of: int = 10) -> list[tuple[str, str, str, int, str]]:
    """Two scorers over `of` campaigns, agreeing on the first `agree`."""
    rows = []
    for c in range(of):
        rows.append((f"c{c}", "a", "approve", 0, "calibration"))
        rows.append((f"c{c}", "b", "approve" if c < agree else "reject", 0, "calibration"))
    return rows


@pytest.mark.parametrize(("agree", "met"), [(10, True), (8, True), (7, False), (0, False)])
def test_the_bar_is_eight_of_ten(agree: int, met: bool) -> None:
    result = calibration_agreement(scores(agree))
    assert (result.scored_by_both, result.agreed, result.met) == (10, agree, met)


def test_nine_campaigns_scored_is_not_a_met_bar() -> None:
    assert calibration_agreement(scores(9, of=9)).met is False


def test_a_rescore_replaces_the_scorers_earlier_decision() -> None:
    rows = scores(8) + [("c9", "b", "approve", 1, "calibration")]  # b re-scores c9 to agree
    assert calibration_agreement(rows).agreed == 9


def test_a_tuning_approval_cannot_be_counted_as_calibration() -> None:
    rows = scores(8) + [("c9", "b", "approve", 1, "tuning")]  # would lift 8 to 9 if counted
    with pytest.raises(ValueError, match="cannot be counted as calibration"):
        calibration_agreement(rows)


def test_the_queries_select_their_own_kind_only() -> None:
    assert "kind = 'calibration'" in CALIBRATION_APPROVALS_SQL
    assert "kind = 'tuning'" in TUNING_APPROVALS_SQL
    assert "calibration" not in TUNING_APPROVALS_SQL.replace("is_calibration", "")
