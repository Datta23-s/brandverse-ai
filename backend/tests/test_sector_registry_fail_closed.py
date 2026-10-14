"""Fail-closed rule of the sector constraint registry (Architecture.md §8.1, Rules.md T4)."""

from datetime import date

from app.core.sector_registry import Sector, SectorConstraint, check_registry_coverage

TODAY = date(2026, 10, 14)
SUPPLEMENTS = Sector("health-supplements", regulated=True)
FOOTWEAR = Sector("footwear", regulated=False)


def _rule(
    sector_id: str, effective: date = date(2026, 1, 1), retired: date | None = None
) -> SectorConstraint:
    return SectorConstraint("r1", sector_id, "prohibited_phrase", effective, retired)


def test_regulated_sector_without_rules_blocks() -> None:
    decision = check_registry_coverage(SUPPLEMENTS, [], TODAY)
    assert decision.blocked
    assert "fail closed" in decision.reason


def test_regulated_sector_with_only_other_sectors_rules_blocks() -> None:
    assert check_registry_coverage(SUPPLEMENTS, [_rule("footwear")], TODAY).blocked


def test_regulated_sector_with_only_retired_rules_blocks() -> None:
    retired = _rule("health-supplements", retired=TODAY)
    assert check_registry_coverage(SUPPLEMENTS, [retired], TODAY).blocked


def test_regulated_sector_with_only_future_rules_blocks() -> None:
    future = _rule("health-supplements", effective=date(2027, 1, 1))
    assert check_registry_coverage(SUPPLEMENTS, [future], TODAY).blocked


def test_regulated_sector_with_active_rule_passes() -> None:
    decision = check_registry_coverage(SUPPLEMENTS, [_rule("health-supplements")], TODAY)
    assert not decision.blocked
    assert decision.active_rule_ids == ("r1",)


def test_rule_retiring_tomorrow_is_still_active_today() -> None:
    rule = _rule("health-supplements", retired=date(2026, 10, 15))
    assert not check_registry_coverage(SUPPLEMENTS, [rule], TODAY).blocked


def test_unregulated_sector_without_rules_passes() -> None:
    assert not check_registry_coverage(FOOTWEAR, [], TODAY).blocked


def test_missing_sector_blocks() -> None:
    decision = check_registry_coverage(None, [_rule("health-supplements")], TODAY)
    assert decision.blocked
