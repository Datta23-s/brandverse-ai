"""How every evaluation run ended, per arm (Rules.md A2: failed and capped runs are reported, not
dropped).

The count of gate-blocked runs is a result in its own right — evidence the truth and safety gates
work — so `blocked` is its own status (migration 0015), never folded into `failed`. This report
counts, per arm, the runs in each status, and keeps `blocked` and `partial` (stopped by the
budget, Rules.md C4) visible as separate figures even when zero. A status the report does not know
fails loudly rather than vanishing from the totals.

Read from `runs` only: status is written by the backend alone (0005 gives API roles no update
policy, and 0016 forces a user-inserted run to start as 'queued'). Scoped to the evaluation
organisations passed in (Rules.md P3), excluding practice organisations, as the approvals and
prompt-version reads are. `since` / `until` bound the period by run creation time.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

RunStatus = Literal["queued", "completed", "partial", "failed", "blocked"]
# Migration 0015's CHECK, in the same order.
RUN_STATUSES: tuple[RunStatus, ...] = ("queued", "completed", "partial", "failed", "blocked")
ARMS = ("baseline_a", "baseline_b", "proposed")

RUN_OUTCOMES_BY_ARM_SQL = """
select r.arm, r.status, count(*)::int
from public.runs r
join public.organisations o on o.id = r.org_id
where r.org_id = any(%(org_ids)s)
  and not o.is_practice
  and (%(since)s::timestamptz is null or r.created_at >= %(since)s::timestamptz)
  and (%(until)s::timestamptz is null or r.created_at < %(until)s::timestamptz)
group by r.arm, r.status
"""


@dataclass(frozen=True, slots=True)
class ArmOutcomes:
    arm: str
    by_status: dict[RunStatus, int]

    @property
    def total(self) -> int:
        return sum(self.by_status.values())

    @property
    def blocked(self) -> int:
        return self.by_status["blocked"]


def summarise(rows: list[tuple[str, str, int]]) -> list[ArmOutcomes]:
    """Every arm, every status, zeros included."""
    counts: dict[str, dict[RunStatus, int]] = {a: dict.fromkeys(RUN_STATUSES, 0) for a in ARMS}
    for arm, status, n in rows:
        if arm not in counts or status not in counts[arm]:
            raise ValueError(f"unknown arm or run status in runs: {arm!r}, {status!r}")
        counts[arm][status] = n
    return [ArmOutcomes(a, counts[a]) for a in ARMS]


async def run_outcomes_by_arm(
    conn: AsyncConnection,
    org_ids: list[UUID],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[ArmOutcomes]:
    cur = await conn.execute(
        RUN_OUTCOMES_BY_ARM_SQL, {"org_ids": org_ids, "since": since, "until": until}
    )
    return summarise([(arm, status, n) for arm, status, n in await cur.fetchall()])
