"""The evaluation harness's only read of approval decisions (docs/evaluation-plan.md).

Calibration decisions (docs/approval-rubric.md §6) live in the same `approvals` table, flagged
`is_calibration`, and are excluded here, in code, not by discipline — as seed memory is
(Memory.md §7.1, Rules.md A3). Practice organisations (`organisations.is_practice`) are excluded
too, so an unflagged row there could not leak even if the database trigger that forbids it were
bypassed, and so are test organisations (`organisations.is_test`, migration 0020: the fixed E2E
organisations and their lasting fixtures). Every filter is a flag, never an id: org ids are
environment-specific and change on a reseed.

Scoped to the evaluation organisations passed in (Rules.md P3).
"""

EVALUATION_APPROVALS_SQL = """
select a.id, a.campaign_id, a.org_id, a.approver_id, a.outcome, a.failed_gate, a.reason,
       a.seconds_spent, a.arm_inferred, a.arm_inference_note, a.decided_at
from approvals a
join organisations o on o.id = a.org_id
where a.org_id = any(%(org_ids)s)
  and not a.is_calibration
  and not o.is_practice
  and not o.is_test
order by a.decided_at, a.id
"""
