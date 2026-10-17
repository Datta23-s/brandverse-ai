"""Sector constraint registry: the fail-closed coverage rule (Architecture.md §8.1, Rules.md T4).

A regulated sector with no active registry rules blocks the run. Missing rules are never read
as "no constraints". A brand with no sector also blocks: its regulated status cannot be known.
Phrase matching against generated copy is the Validation Engine's job (Phase 3), not this module's.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

ConstraintKind = Literal["required_disclaimer", "prohibited_phrase"]


@dataclass(frozen=True)
class Sector:
    id: str
    regulated: bool


@dataclass(frozen=True)
class SectorConstraint:
    id: str
    sector_id: str
    kind: ConstraintKind
    effective_from: date
    retired_at: date | None = None
    match_type: Literal["phrase", "regex"] = "phrase"
    # prohibited_phrase: wording that must not appear; required_disclaimer: trigger (None = always).
    pattern: str | None = None

    def is_active(self, on: date) -> bool:
        """Active from effective_from (inclusive) until retired_at (exclusive)."""
        return self.effective_from <= on and (self.retired_at is None or on < self.retired_at)


@dataclass(frozen=True)
class CoverageDecision:
    blocked: bool
    reason: str
    active_rule_ids: tuple[str, ...]


def check_registry_coverage(
    sector: Sector | None, rules: Iterable[SectorConstraint], on: date
) -> CoverageDecision:
    """Decide whether a run may proceed given the registry rules in force on `on`."""
    if sector is None:
        return CoverageDecision(True, "brand has no sector; registry coverage cannot be shown", ())

    active = tuple(r.id for r in rules if r.sector_id == sector.id and r.is_active(on))
    if sector.regulated and not active:
        return CoverageDecision(
            True, f"regulated sector '{sector.id}' has no active registry rules (fail closed)", ()
        )
    return CoverageDecision(False, "registry coverage satisfied", active)
