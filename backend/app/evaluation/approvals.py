"""The evaluation harness's only read of approval decisions (docs/evaluation-plan.md).

Calibration decisions (docs/approval-rubric.md §6) live in the same `approvals` table, flagged
`is_calibration`. Tuning decisions (the tuning organisation that measures the strong calls a run
takes) live there too as a THIRD KIND, not flagged and never counted as calibration (migration
0032: `kind` is evaluation, calibration or tuning; the calibration figure counts calibration only).
Both are excluded here, in code, not by discipline, as seed memory is (Memory.md §7.1, Rules.md
A3). Practice organisations (`organisations.is_practice`) are excluded too, so a row there could
not leak even if the database trigger that governs it were bypassed, and so are test
organisations (`organisations.is_test`, migration 0020: the fixed E2E organisations and their
lasting fixtures). Every filter is a flag, never an id: org ids are environment-specific and
change on a reseed.

Scoped to the evaluation organisations passed in (Rules.md P3).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

EVALUATION_APPROVALS_SQL = """
select a.id, a.campaign_id, a.org_id, a.approver_id, a.outcome, a.failed_gate, a.reason,
       a.seconds_spent, a.arm_inferred, a.arm_inference_note, a.decided_at
from approvals a
join organisations o on o.id = a.org_id
where a.org_id = any(%(org_ids)s)
  and not a.is_calibration
  and a.kind = 'evaluation'
  and not o.is_practice
  and not o.is_test
order by a.decided_at, a.id
"""

# ── The calibration figure counts calibration approvals only (migration 0032) ────────────────

CALIBRATION_BAR = 8  # of CALIBRATION_SIZE campaigns the two scorers must agree on (rubric §6)
CALIBRATION_SIZE = 10

CALIBRATION_APPROVALS_SQL = """
select a.campaign_id, a.approver_id, a.outcome, a.decided_at, a.kind
from approvals a
join organisations o on o.id = a.org_id
where a.org_id = any(%(org_ids)s)
  and a.kind = 'calibration'
  and o.is_practice
  and not o.is_test
order by a.decided_at, a.id
"""

# Reported on their own, never added to the calibration figure.
TUNING_APPROVALS_SQL = CALIBRATION_APPROVALS_SQL.replace("'calibration'", "'tuning'")

# The reads that include practice organisations ON PURPOSE, and the only ones: every other read in
# app/evaluation excludes them. The exemption is a declared list that a test holds to account
# (tests/db/test_test_organisations.py): a practice read must say so, read exactly one
# non-evaluation kind, and still exclude test organisations.
PRACTICE_READS: frozenset[str] = frozenset({"CALIBRATION_APPROVALS_SQL", "TUNING_APPROVALS_SQL"})


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    scored_by_both: int
    agreed: int

    @property
    def met(self) -> bool:
        """The bar: the scorers agreed on at least 8 of the 10 practice campaigns."""
        return self.scored_by_both == CALIBRATION_SIZE and self.agreed >= CALIBRATION_BAR


def calibration_agreement(rows: Iterable[tuple[Any, Any, str, Any, str]]) -> CalibrationResult:
    """Agreement between the two scorers over rows of CALIBRATION_APPROVALS_SQL, in decision order.

    Each scorer's LATEST decision on a campaign counts (a re-score is a new row, rubric §6). A row
    whose kind is not `calibration` is refused outright rather than skipped: the query already
    filters on it, so one arriving here means a tuning approval is being counted as calibration."""
    latest: dict[Any, dict[Any, str]] = {}
    for campaign_id, approver_id, outcome, _decided_at, kind in rows:
        if kind != "calibration":
            raise ValueError(f"a {kind!r} approval cannot be counted as calibration")
        latest.setdefault(campaign_id, {})[approver_id] = outcome
    both = [outcomes for outcomes in latest.values() if len(outcomes) == 2]
    agreed = sum(1 for outcomes in both if len(set(outcomes.values())) == 1)
    return CalibrationResult(scored_by_both=len(both), agreed=agreed)
