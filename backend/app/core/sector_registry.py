"""Sector constraint registry: the fail-closed coverage rule (Architecture.md §8.1, Rules.md T4).

A regulated sector with no active registry rules blocks the run. Missing rules are never read
as "no constraints". A brand with no sector also blocks: its regulated status cannot be known.
Matching rules against generated copy is the Validation Engine's job (app/core/gates.py).

**"Verbatim" for a required disclaimer — the declared normalisation rule (2026-10-17).** A
disclaimer is present when `verbatim_form(disclaimer_text)` occurs in `verbatim_form(copy)`.
`verbatim_form` does exactly three things and nothing else:
1. Unicode NFC — canonically equivalent encodings (a precomposed "é" vs "e" + combining accent)
   compare equal; they render identically, so a reader cannot tell them apart.
2. Every run of whitespace (spaces, tabs, newlines, non-breaking and other Unicode spaces) becomes
   one ordinary space — line wrapping and a doubled space are layout, not wording.
3. Leading and trailing whitespace is stripped — a trailing space must never fail a gate.
It is **case-sensitive** and leaves **punctuation and every other character unchanged**: case and
punctuation are part of prescribed wording, so "consult a doctor" for "Consult a doctor." is an
altered disclaimer, and the gate reports it as altered — a finding distinct from "missing" — so a
block is never for an invisible reason. Phrase matching for prohibited wording is looser on
purpose (case- and punctuation-insensitive, app/core/text_match.py): it must catch variants,
whereas a disclaimer must be the prescribed text.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

_WHITESPACE = re.compile(r"\s+")


def verbatim_form(text: str) -> str:
    """The form in which a required disclaimer is compared (module docstring: NFC, whitespace
    runs collapsed to one space, ends stripped; case and punctuation kept)."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


ConstraintKind = Literal["required_disclaimer", "prohibited_phrase"]
# Where a sector's rules come from (migration 0021). 'illustrative' rules are synthetic, written to
# exercise the mechanism; they are not derived from any regulation.
RulesProvenance = Literal["authoritative", "illustrative"]


@dataclass(frozen=True)
class Sector:
    id: str
    regulated: bool
    # Defaults to the safe reading: nothing is presented as authoritative unless the row says so.
    rules_provenance: RulesProvenance = "illustrative"

    def describe(self) -> str:
        """The sector as every report must name it: with its rules' provenance, so an illustrative
        rule can never be mistaken for a regulatory one."""
        kind = "regulated" if self.regulated else "not regulated"
        rules = (
            "authoritative rules"
            if self.rules_provenance == "authoritative"
            else "illustrative rules: synthetic, not derived from any regulation"
        )
        return f"'{self.id}' ({kind}; {rules})"


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
    # required_disclaimer only: the exact text that must appear (verbatim; app/core/gates.py).
    disclaimer_text: str | None = None

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
            True,
            f"regulated sector {sector.describe()} has no active registry rules (fail closed)",
            (),
        )
    return CoverageDecision(False, "registry coverage satisfied", active)
