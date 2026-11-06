"""Which evaluation campaigns contained a provider retry, and the sensitivity check.

(Owner, 2026-11-06; docs/evaluation-plan.md Amendment 2, "a successful answer after a retry is
kept".) A request that was answered after a provider-unavailable response is a RESULT and is kept:
discarding successes would cost enormous re-runs to remove a speculative effect. But a retry issued
right after a provider failure might itself have been degraded, so those campaigns are FLAGGED,
derivable at any time from `llm_calls` (a count, never a counter: Rules.md A1), and the analysis
reports every headline figure twice, with the flagged campaigns and without them. Honesty without
the re-run cost.

A flagged campaign is a run with at least one `llm_calls` row whose outcome is
`provider_unavailable` (the row of an answer that was 5xx; the retry that followed is its own row).
A run that FAILED on the provider is not this table's concern: that is an infrastructure failure
and is re-run.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from statistics import fmean
from uuid import UUID

from psycopg import AsyncConnection

# Evaluation organisations only, like every other evaluation read.
PROVIDER_RETRIES_SQL = """
select c.run_id, r.arm, count(*)::int as unavailable_rows, min(c.created_at) as first_at
from public.llm_calls c
join public.runs r on r.id = c.run_id and r.org_id = c.org_id
join public.organisations o on o.id = c.org_id
where c.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and c.outcome = 'provider_unavailable'
group by c.run_id, r.arm
order by first_at
"""


@dataclass(frozen=True, slots=True)
class RetryFlag:
    run_id: UUID
    arm: str
    unavailable_rows: int


async def runs_with_provider_retry(
    conn: AsyncConnection, org_ids: Collection[UUID]
) -> list[RetryFlag]:
    cur = await conn.execute(PROVIDER_RETRIES_SQL, {"org_ids": list(org_ids)})
    return [RetryFlag(run_id, arm, int(n)) async for run_id, arm, n, _first in cur]


@dataclass(frozen=True, slots=True)
class Sensitivity:
    """One figure, twice: over every campaign, and over the campaigns with no provider retry."""

    n_all: int
    mean_all: float | None
    n_without: int
    mean_without: float | None
    n_flagged: int

    @property
    def moved_by(self) -> float | None:
        """How far dropping the flagged campaigns moves the mean (None if either is empty)."""
        if self.mean_all is None or self.mean_without is None:
            return None
        return self.mean_without - self.mean_all


def sensitivity(values: Mapping[UUID, float], flagged: Collection[UUID]) -> Sensitivity:
    """The sensitivity check: the mean of `values` (one per run) with and without the flagged
    runs. A run in `flagged` that has no value is ignored."""
    flagged_set = set(flagged)
    without = [v for run, v in values.items() if run not in flagged_set]
    return Sensitivity(
        n_all=len(values),
        mean_all=fmean(values.values()) if values else None,
        n_without=len(without),
        mean_without=fmean(without) if without else None,
        n_flagged=sum(1 for run in values if run in flagged_set),
    )
