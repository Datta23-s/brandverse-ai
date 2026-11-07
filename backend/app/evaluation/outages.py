"""Provider outage windows, derived from the call log, and the runs that overlapped one.

Owner ruling, 5 November 2026: record every outage window observed, so any run that touched one can
be identified afterwards. Since migration 0047 a call the provider answered with a 5xx is a row with
`outcome = 'provider_unavailable'`, so the windows are DERIVED from the rows, not remembered. A run
that overlapped a window is identified, not discarded: the attempt rule (amended 2026-11-05) treats
an attempt ended by the outage as an infrastructure failure and re-runnable.

A window is a maximal run of provider-unavailable rows with no gap longer than `gap` between
neighbours. Reads only the evaluation's own organisations (Rules.md P3), excluding practice and test
organisations, like every other evaluation read.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection

DEFAULT_GAP = timedelta(minutes=15)

OUTAGE_ROWS_SQL = """
select c.created_at
from public.llm_calls c
join public.organisations o on o.id = c.org_id
where c.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and c.outcome = 'provider_unavailable'
order by c.created_at
"""

RUN_SPANS_SQL = """
select r.id, r.created_at, coalesce(r.completed_at, now())
from public.runs r
join public.organisations o on o.id = r.org_id
where r.org_id = any(%(org_ids)s) and not o.is_practice and not o.is_test
order by r.created_at
"""

# The waits for a person (migration 0036), the same table `latency` reads. A parked run is waiting
# for its approver, possibly for days; it is not running, so an outage in that time did not touch
# it (owner, 2026-11-07: without this nearly every parked run is flagged).
RUN_WAITS_SQL = """
select w.run_id, w.parked_at, w.resumed_at
from public.run_waits w
join public.runs r on r.id = w.run_id and r.org_id = w.org_id
join public.organisations o on o.id = r.org_id
where r.org_id = any(%(org_ids)s) and not o.is_practice and not o.is_test
order by w.run_id, w.parked_at
"""


@dataclass(frozen=True, slots=True)
class OutageWindow:
    start: datetime
    end: datetime
    calls: int

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return start <= self.end and end >= self.start


def outage_windows(times: Iterable[datetime], gap: timedelta = DEFAULT_GAP) -> list[OutageWindow]:
    """Cluster the times of provider-unavailable calls into windows."""
    ordered = sorted(times)
    windows: list[OutageWindow] = []
    for t in ordered:
        if windows and t - windows[-1].end <= gap:
            last = windows[-1]
            windows[-1] = OutageWindow(last.start, t, last.calls + 1)
        else:
            windows.append(OutageWindow(t, t, 1))
    return windows


def active_spans(
    start: datetime, end: datetime, waits: Iterable[tuple[datetime, datetime | None]]
) -> list[tuple[datetime, datetime]]:
    """A run's time MINUS its waits for a person: the stretches in which it was running.

    `waits` are (parked_at, resumed_at) pairs; an open wait (resumed_at None) runs to the end of the
    span, so nothing after it counts. A run that never parked is one span, start to end."""
    spans: list[tuple[datetime, datetime]] = []
    cursor = start
    for parked, resumed in sorted(waits, key=lambda w: w[0]):
        if parked > cursor:
            spans.append((cursor, min(parked, end)))
        cursor = max(cursor, end if resumed is None else resumed)
    if cursor < end:
        spans.append((cursor, end))
    return [(a, b) for a, b in spans if a <= b]


def runs_touching(
    windows: Sequence[OutageWindow], spans: Iterable[tuple[UUID, datetime, datetime]]
) -> dict[UUID, list[OutageWindow]]:
    """{run_id: the windows it overlapped} for every run that overlapped at least one. A run may
    appear in `spans` more than once (its stretches between waits); the windows are collected."""
    touched: dict[UUID, list[OutageWindow]] = {}
    for run_id, start, end in spans:
        for w in windows:
            if w.overlaps(start, end) and w not in touched.setdefault(run_id, []):
                touched[run_id].append(w)
    return {run_id: hit for run_id, hit in touched.items() if hit}


async def evaluation_outages(
    conn: AsyncConnection, org_ids: list[UUID], gap: timedelta = DEFAULT_GAP
) -> tuple[list[OutageWindow], dict[UUID, list[OutageWindow]]]:
    """(windows, runs that overlapped one) for the given evaluation organisations."""
    cur = await conn.execute(OUTAGE_ROWS_SQL, {"org_ids": org_ids})
    windows = outage_windows([row[0] for row in await cur.fetchall()], gap)
    cur = await conn.execute(RUN_WAITS_SQL, {"org_ids": org_ids})
    waits: dict[UUID, list[tuple[datetime, datetime | None]]] = {}
    for run_id, parked, resumed in await cur.fetchall():
        waits.setdefault(run_id, []).append((parked, resumed))
    cur = await conn.execute(RUN_SPANS_SQL, {"org_ids": org_ids})
    spans = [
        (row[0], a, b)
        for row in await cur.fetchall()
        for a, b in active_spans(row[1], row[2], waits.get(row[0], ()))
    ]
    return windows, runs_touching(windows, spans)
