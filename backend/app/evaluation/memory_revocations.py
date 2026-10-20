"""How memory items died: by rule (confidence decay) or by a person (docs/evaluation-plan.md,
"Memory revocations by kind").

An item revoked by rule and an item revoked by a person are different events, and how many died
each way is a result about the memory system. The split is a property of the row, not a reading
of free text: migration 0024 makes `revoked_by` null if and only if `revoked_reason` is a decay
record (`confidence_decay: …; cause <cause>:…; run …`), and a person can never write one. Decay is
further split by its cause — a validation-engine flag, or a reviewer's edit-out — read from the
record's constrained `cause` field.

Scoped like the other evaluation reads (app/evaluation/run_outcomes.py): the organisations passed
in (Rules.md P3), practice and test organisations excluded by flag, and seed items excluded
(Memory.md §7.1: seed memory is kept apart from the evaluation). `since` / `until` bound the period
by revocation time. A row of a kind the report does not know fails loudly rather than vanishing.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

RevocationKind = Literal["decay_validation_flag", "decay_reviewer_edit", "review"]
REVOCATION_KINDS: tuple[RevocationKind, ...] = (
    "decay_validation_flag",
    "decay_reviewer_edit",
    "review",
)

MEMORY_REVOCATIONS_SQL = """
select case when m.revoked_by is not null then 'review'
            else 'decay_' || (pg_catalog.regexp_match(m.revoked_reason, '; cause ([a-z_]+):'))[1]
       end as kind,
       count(*)::int
from public.memory_items m
join public.organisations o on o.id = m.org_id
where m.revoked
  and m.org_id = any(%(org_ids)s)
  and not m.is_seed
  and not o.is_practice
  and not o.is_test
  and (%(since)s::timestamptz is null or m.revoked_at >= %(since)s::timestamptz)
  and (%(until)s::timestamptz is null or m.revoked_at < %(until)s::timestamptz)
group by 1
"""


@dataclass(frozen=True, slots=True)
class Revocations:
    by_kind: dict[RevocationKind, int]

    @property
    def by_decay(self) -> int:
        return self.by_kind["decay_validation_flag"] + self.by_kind["decay_reviewer_edit"]

    @property
    def by_review(self) -> int:
        return self.by_kind["review"]


def summarise(rows: list[tuple[str | None, int]]) -> Revocations:
    """Every kind, zeros included."""
    counts: dict[RevocationKind, int] = dict.fromkeys(REVOCATION_KINDS, 0)
    known: dict[str | None, RevocationKind] = {k: k for k in REVOCATION_KINDS}
    for kind, n in rows:
        if kind not in known:
            raise ValueError(f"unknown memory revocation kind: {kind!r}")
        counts[known[kind]] = n
    return Revocations(counts)


async def memory_revocations(
    conn: AsyncConnection,
    org_ids: list[UUID],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Revocations:
    cur = await conn.execute(
        MEMORY_REVOCATIONS_SQL, {"org_ids": org_ids, "since": since, "until": until}
    )
    return summarise([(kind, n) for kind, n in await cur.fetchall()])
