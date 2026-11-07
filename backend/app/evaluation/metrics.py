"""The four headline figures (ARR, SRR, CR, LR) and what must always be printed beside them.

Registered in `docs/evaluation-plan.md` section 4; built 7 November 2026 (decision-log 2.53), before
generation, so no figure is defined after seeing a number.

WHAT IS COUNTED, AND WHAT IS REFUSED
* **Arms are paired per REQUEST.** A reduction is baseline minus proposed for the SAME request, then
  aggregated; a ratio of two arms' totals over different request sets would mix the request mix into
  the result. A request missing any arm's primary attempt is NOT paired: it is listed as incomplete
  and excluded from every figure (never silently dropped).
* **Only the PRIMARY attempt of a (request, arm) counts.** The attempt rule (2026-10-17, Amendment 1
  of 5 November): the primary result is the first attempt that ended without infrastructure failure.
  An invalidated attempt and a `superseded` one are excluded from ARR, SRR, CR, LR and from the
  decomposition. `select_primary` is the single place that decides it, and it reports WHY each
  excluded run was excluded. It also cross-checks `runs.supersedes_run_id` (migration 0048) against
  its own choice and reports any disagreement rather than preferring one.
* **CALLS are WORK, not spend** (`llm_attempts.totals`): the last attempt of each (step, revision
  round, agent), never an orphaned or provider-unavailable row. A re-executed step spent twice and
  worked once, and ARR is a statement about work.
* **Cost is list-price-derived, not money paid** (`Rules.md` C0), and every cost figure says so.

LR IS PARTLY DERIVED, AND SAYS SO (owner, 2026-10-28 and 2026-10-29). Replay removes the web, so the
search and fetch components are COUNT DIFFERENCES times unit costs measured once from the recording,
and the model and local-research components are observed in each run. `LatencyReduction` keeps the
four apart and labels the total partly derived; `UnitCosts.from_snapshot` reads the unit costs from
the committed recording lines (sum of `fetch_wall_s` over sum of `pages_fetched_live`), so they are
not typed in by hand.

ALWAYS TOGETHER (owner, 2026-11-06; decision-log 2.44): depth REDUCTION by campaign 3 and three-band
SEPARATION, each beside the other, with the memory-versus-novelty attribution (2.45). `Report.lines`
emits them in one block and there is no way to render one without the others.
"""

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection

from app.evaluation.llm_attempts import CallRow, llm_calls_by_attempt, totals
from app.evaluation.necessity_decomposition import (
    RANK,
    Attribution,
    Bands,
    Decomposition,
    attribute,
    from_record,
)
from app.evaluation.outages import evaluation_outages
from app.evaluation.reruns import Invalidation
from app.evaluation.reruns import invalidations as list_invalidations
from app.evaluation.test_set import request_index, request_inputs

BASELINE = "baseline_b"  # "Baseline is Baseline B unless stated" (evaluation-plan section 4)
ARMS = ("baseline_a", "baseline_b", "proposed")
Position = Literal[1, 2, 3]

# A run's own ending, as the attempt rule reads it.
#   result         it ended without infrastructure failure: a candidate for the primary attempt
#   invalidated    an infrastructure failure ended it: never a result, re-run instead
#   unfinished     it never reached an outcome (queued, or still parked): not yet anything
Ending = Literal["result", "invalidated", "unfinished"]


def ending(status: str, failure: Mapping[str, Any] | None) -> Ending:
    """What a run's row says about its ending. `blocked` and `partial` are RESULTS (a gate block is
    a result in its own right, `run_outcomes.py`; a budget stop is a capped run, Rules.md C4)."""
    if status in ("queued", "awaiting_approval"):
        return "unfinished"
    if status == "failed" and (failure or {}).get("infrastructure_failure"):
        return "invalidated"
    return "result"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One run as the metrics read it, from `runs`, `campaigns` and `llm_calls`."""

    run_id: UUID
    arm: str
    request_id: str
    position: int
    brand_id: str
    status: str
    failure: Mapping[str, Any] | None
    created_at: datetime
    searches: int | None
    pages_fetched: int | None
    depth: str | None
    timings: Mapping[str, float] | None
    necessity: Mapping[str, Any] | None
    # True when another run records this one as the run it replaced (migration 0048).
    superseded: bool
    supersedes_run_id: UUID | None
    provider_retry: bool
    work_calls: int
    work_cost_usd: float
    model_ms: float

    @property
    def ending(self) -> Ending:
        return ending(self.status, self.failure)

    @property
    def local_research_ms(self) -> float | None:
        """Replayed fetch, extraction and scoring together. NOT extraction alone: nothing records
        extraction separately (a finding, decision-log 2.53), so it is reported under this name."""
        return None if self.timings is None else float(self.timings.get("research_other_ms", 0.0))


# ── the primary attempt ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Excluded:
    run_id: UUID
    request_id: str
    arm: str
    why: str


@dataclass(frozen=True, slots=True)
class Selection:
    primary: dict[tuple[str, str], RunRecord]  # (request_id, arm) -> the primary attempt
    excluded: list[Excluded]
    # Where `runs.supersedes_run_id` and this selection disagree. Never resolved silently.
    linkage_disagreements: list[str] = field(default_factory=list)


def select_primary(runs: Iterable[RunRecord]) -> Selection:
    """The primary attempt of each (request, arm): the EARLIEST run that ended without
    infrastructure failure. Everything else is excluded with its reason."""
    by_pair: dict[tuple[str, str], list[RunRecord]] = {}
    for run in runs:
        by_pair.setdefault((run.request_id, run.arm), []).append(run)
    primary: dict[tuple[str, str], RunRecord] = {}
    excluded: list[Excluded] = []
    disagreements: list[str] = []
    for pair, attempts in by_pair.items():
        ordered = sorted(attempts, key=lambda r: (r.created_at, str(r.run_id)))
        chosen: RunRecord | None = None
        for run in ordered:
            if chosen is None and run.ending == "result":
                chosen = run
                continue
            excluded.append(
                Excluded(
                    run.run_id,
                    run.request_id,
                    run.arm,
                    {
                        "invalidated": "an infrastructure failure ended it: re-run, not a result",
                        "unfinished": f"it never reached an outcome (status {run.status!r})",
                        "result": "a later attempt of a request whose primary is an earlier run",
                    }[run.ending],
                )
            )
        if chosen is not None:
            primary[pair] = chosen
        # The stored linkage must agree with the rule: a chosen run is not superseded, and an
        # invalidated run that was re-run should carry the link (migration 0048).
        if chosen is not None and chosen.superseded:
            disagreements.append(
                f"{pair[0]}:{pair[1]}: run {chosen.run_id} is the primary attempt by the rule but "
                "another run records it as the run it replaced"
            )
        invalid = [r for r in ordered if r.ending == "invalidated"]
        linked = {r.supersedes_run_id for r in ordered if r.supersedes_run_id is not None}
        for run in invalid:
            if len(ordered) > 1 and run.run_id not in linked:
                disagreements.append(
                    f"{pair[0]}:{pair[1]}: run {run.run_id} was invalidated and re-run, but no run "
                    "records it as the one it replaced (runs.supersedes_run_id)"
                )
    return Selection(primary, excluded, disagreements)


# ── the paired requests ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PairedRequest:
    request_id: str
    position: int
    brand_id: str
    arms: Mapping[str, RunRecord]  # every arm present, keyed by arm

    @property
    def provider_retry(self) -> bool:
        """True when ANY arm of this request answered after a provider 5xx: the campaign is flagged
        and every figure is reported with and without it (Amendment 2)."""
        return any(r.provider_retry for r in self.arms.values())


def pair_requests(
    selection: Selection, arms: Sequence[str] = ARMS
) -> tuple[list[PairedRequest], list[str]]:
    """(requests with a primary attempt in EVERY arm, the incomplete ones with what they lack)."""
    index = request_index()
    by_request: dict[str, dict[str, RunRecord]] = {}
    for (request_id, arm), run in selection.primary.items():
        by_request.setdefault(request_id, {})[arm] = run
    paired: list[PairedRequest] = []
    incomplete: list[str] = []
    for request_id in sorted(by_request):
        found = by_request[request_id]
        missing = [a for a in arms if a not in found]
        if missing:
            incomplete.append(f"{request_id}: no primary attempt for {', '.join(missing)}")
            continue
        meta = index[request_id]
        paired.append(PairedRequest(request_id, meta.position, meta.brand_id, dict(found)))
    return paired, incomplete


# ── the four figures ──────────────────────────────────────────────────────────────────────────


def _reduction(baseline: float, proposed: float) -> float | None:
    """(baseline - proposed) / baseline x 100, or None when the baseline is zero (undefined, never
    reported as 0 or 100)."""
    return None if baseline == 0 else (baseline - proposed) / baseline * 100.0


@dataclass(frozen=True, slots=True)
class ArmTotals:
    runs: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    searches: int = 0
    pages_fetched: int = 0
    model_ms: float = 0.0
    local_research_ms: float = 0.0
    # Runs whose column was null: the figure above is a sum over the others, and this says how many
    # it could not include (unknown, never zero).
    searches_unknown: int = 0
    pages_unknown: int = 0
    timings_unknown: int = 0


def arm_totals(runs: Iterable[RunRecord]) -> ArmTotals:
    rows = list(runs)
    return ArmTotals(
        runs=len(rows),
        calls=sum(r.work_calls for r in rows),
        cost_usd=round(sum(r.work_cost_usd for r in rows), 6),
        searches=sum(r.searches or 0 for r in rows),
        pages_fetched=sum(r.pages_fetched or 0 for r in rows),
        model_ms=sum(r.model_ms for r in rows),
        local_research_ms=sum(r.local_research_ms or 0.0 for r in rows),
        searches_unknown=sum(1 for r in rows if r.searches is None),
        pages_unknown=sum(1 for r in rows if r.pages_fetched is None),
        timings_unknown=sum(1 for r in rows if r.timings is None),
    )


@dataclass(frozen=True, slots=True)
class UnitCosts:
    """Measured ONCE from the recording, not from the evaluation's runs (replay removed the web)."""

    seconds_per_search: float
    fetch_wall_s_per_page: float
    pages_fetched_live: int
    searches_measured: int
    source: str

    @classmethod
    def from_snapshot(cls, committed: Path, seconds_per_search: float) -> "UnitCosts":
        """`fetch_wall_s` over `pages_fetched_live`, summed over the committed recording lines, as
        the plan registers. `seconds_per_search` is the recording's measured median, passed in
        because the committed lines hold the fetch wall time and not the per-search duration."""
        wall = 0.0
        pages = 0
        searches = 0
        for line in committed.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("fetch_wall_s") is not None and row.get("pages_fetched_live"):
                wall += float(row["fetch_wall_s"])
                pages += int(row["pages_fetched_live"])
            searches += int(row.get("searches_new", 0) or 0)
        if pages == 0:
            raise ValueError(
                f"{committed} holds no line with both fetch_wall_s and pages_fetched_live: the "
                "fetch unit cost cannot be measured (R01 to R12 were recorded before the "
                "instrumentation; decision-log 2.53)"
            )
        return cls(
            seconds_per_search=seconds_per_search,
            fetch_wall_s_per_page=wall / pages,
            pages_fetched_live=pages,
            searches_measured=searches,
            source=str(committed),
        )


@dataclass(frozen=True, slots=True)
class LatencyReduction:
    """LR in its four parts, two derived and two observed, never added without the label."""

    search_derived_s: float
    fetch_derived_s: float
    model_observed_s: float
    local_research_observed_s: float
    unit_costs: UnitCosts

    @property
    def total_s(self) -> float:
        return (
            self.search_derived_s
            + self.fetch_derived_s
            + self.model_observed_s
            + self.local_research_observed_s
        )

    def lines(self) -> list[str]:
        return [
            f"LR search  {self.search_derived_s:+.1f} s  DERIVED "
            f"(count difference x {self.unit_costs.seconds_per_search:.2f} s per search)",
            f"LR fetch   {self.fetch_derived_s:+.1f} s  DERIVED "
            f"(count difference x {self.unit_costs.fetch_wall_s_per_page:.2f} s per page at "
            f"concurrency 8, {self.unit_costs.source})",
            f"LR model   {self.model_observed_s:+.1f} s  OBSERVED",
            f"LR local research {self.local_research_observed_s:+.1f} s  OBSERVED "
            "(replayed fetch, extraction and scoring together: extraction is not timed alone)",
            f"LR total   {self.total_s:+.1f} s  PARTLY DERIVED (a sum of components, not a "
            "prediction of a live stopwatch)",
        ]


@dataclass(frozen=True, slots=True)
class Efficiency:
    """The four headline figures for one arm against the baseline, over the paired requests."""

    arm: str
    baseline_arm: str
    requests: int
    arr: float | None
    srr: float | None
    cr: float | None
    lr: LatencyReduction | None
    proposed_totals: ArmTotals
    baseline_totals: ArmTotals

    def lines(self) -> list[str]:
        def pct(x: float | None) -> str:
            return "undefined (baseline is zero)" if x is None else f"{x:+.1f}%"

        out = [
            f"{self.arm} against {self.baseline_arm}, {self.requests} paired requests",
            f"  ARR {pct(self.arr)}  (work calls {self.baseline_totals.calls} -> "
            f"{self.proposed_totals.calls})",
            f"  SRR {pct(self.srr)}  (searches {self.baseline_totals.searches} -> "
            f"{self.proposed_totals.searches}; WITH the mandatory fresh query)",
            f"  CR  {pct(self.cr)}  (list-price-equivalent USD {self.baseline_totals.cost_usd:.4f}"
            f" -> {self.proposed_totals.cost_usd:.4f}; no money was paid)",
        ]
        out += [
            f"  {line}"
            for line in (self.lr.lines() if self.lr is not None else ["LR: no unit costs given"])
        ]
        for label, totals_ in (
            ("baseline", self.baseline_totals),
            (self.arm, self.proposed_totals),
        ):
            if totals_.searches_unknown or totals_.pages_unknown or totals_.timings_unknown:
                out.append(
                    f"  INCOMPLETE INPUTS ({label}): searches unknown in "
                    f"{totals_.searches_unknown} run(s), pages in {totals_.pages_unknown}, "
                    f"timings in {totals_.timings_unknown}"
                )
        return out


def efficiency(
    paired: Sequence[PairedRequest],
    arm: str,
    *,
    baseline_arm: str = BASELINE,
    unit_costs: UnitCosts | None = None,
) -> Efficiency:
    """ARR, SRR, CR and LR for `arm` against `baseline_arm` over the paired requests."""
    mine = arm_totals(p.arms[arm] for p in paired)
    base = arm_totals(p.arms[baseline_arm] for p in paired)
    lr = None
    if unit_costs is not None:
        lr = LatencyReduction(
            search_derived_s=(base.searches - mine.searches) * unit_costs.seconds_per_search,
            fetch_derived_s=(base.pages_fetched - mine.pages_fetched)
            * unit_costs.fetch_wall_s_per_page,
            model_observed_s=(base.model_ms - mine.model_ms) / 1000.0,
            local_research_observed_s=(base.local_research_ms - mine.local_research_ms) / 1000.0,
            unit_costs=unit_costs,
        )
    return Efficiency(
        arm=arm,
        baseline_arm=baseline_arm,
        requests=len(paired),
        arr=_reduction(base.calls, mine.calls),
        srr=_reduction(base.searches, mine.searches),
        cr=_reduction(base.cost_usd, mine.cost_usd),
        lr=lr,
        proposed_totals=mine,
        baseline_totals=base,
    )


def by_position(
    paired: Sequence[PairedRequest],
    arm: str,
    *,
    baseline_arm: str = BASELINE,
    unit_costs: UnitCosts | None = None,
) -> dict[int, Efficiency]:
    """The same figures per campaign position (1, 2, 3): where the memory effect is expected."""
    out: dict[int, Efficiency] = {}
    for position in sorted({p.position for p in paired}):
        subset = [p for p in paired if p.position == position]
        out[position] = efficiency(subset, arm, baseline_arm=baseline_arm, unit_costs=unit_costs)
    return out


@dataclass(frozen=True, slots=True)
class Sensitivity:
    """Every headline figure twice: over all paired requests, and over those with no provider retry
    (Amendment 2: a success after a retry is KEPT and FLAGGED, and the figure is reported both
    ways, so honesty costs no re-runs)."""

    all_requests: Efficiency
    without_flagged: Efficiency
    flagged_requests: int

    def lines(self) -> list[str]:
        out = ["with every paired request:", *[f"  {x}" for x in self.all_requests.lines()]]
        out += [
            f"without the {self.flagged_requests} campaign(s) that answered after a provider 5xx:",
            *[f"  {x}" for x in self.without_flagged.lines()],
        ]
        return out


def sensitivity(
    paired: Sequence[PairedRequest],
    arm: str,
    *,
    baseline_arm: str = BASELINE,
    unit_costs: UnitCosts | None = None,
) -> Sensitivity:
    clean = [p for p in paired if not p.provider_retry]
    return Sensitivity(
        all_requests=efficiency(paired, arm, baseline_arm=baseline_arm, unit_costs=unit_costs),
        without_flagged=efficiency(clean, arm, baseline_arm=baseline_arm, unit_costs=unit_costs),
        flagged_requests=len(paired) - len(clean),
    )


# ── depth reduction, three-band separation, and the attribution: always together ──────────────


@dataclass(frozen=True, slots=True)
class DepthResult:
    """Per brand, the Proposed arm's depth at campaigns 1, 2 and 3, and what they show."""

    depths_by_brand: Mapping[str, Sequence[str]]
    attribution: Attribution

    @property
    def brands_complete(self) -> int:
        return sum(1 for d in self.depths_by_brand.values() if len(d) == 3)

    @property
    def reduced(self) -> dict[str, bool]:
        """Campaign 3 shallower than campaign 1 (the property the hypothesis requires)."""
        return {b: RANK[d[2]] < RANK[d[0]] for b, d in self.depths_by_brand.items() if len(d) == 3}

    @property
    def separated(self) -> dict[str, bool]:
        """Three distinct bands, campaign 1 > 2 > 3 (the stricter stage-2 property)."""
        return {
            b: RANK[d[0]] > RANK[d[1]] > RANK[d[2]]
            for b, d in self.depths_by_brand.items()
            if len(d) == 3
        }

    def lines(self) -> list[str]:
        """The pair, and the attribution, in ONE block: there is no way to print one alone."""
        n = self.brands_complete
        return [
            f"depth REDUCTION by campaign 3: {sum(self.reduced.values())} of {n} brands",
            f"three-band SEPARATION (1 > 2 > 3): {sum(self.separated.values())} of {n} brands",
            "  (reported together, always: reduction alone overstates and separation alone "
            "understates the hypothesis; decision-log 2.44)",
            f"memory versus novelty: {self.attribution.line()}",
            "  (the share follows from the FROZEN bands and weights; if they change it is "
            "recomputed, not carried over; decision-log 2.45)",
        ]


def depth_result(
    paired: Sequence[PairedRequest], bands: Bands, weights: Mapping[Any, float]
) -> DepthResult:
    """The Proposed arm's depths per brand and the memory-versus-novelty attribution, from the
    primary attempts only (the engine's own logged record)."""
    depths: dict[str, dict[int, str]] = {}
    decompositions: list[Decomposition] = []
    for p in paired:
        run = p.arms["proposed"]
        if run.depth is not None:
            depths.setdefault(run.brand_id, {})[p.position] = run.depth
        record = run.necessity
        if record is not None and "novelty" in record and p.position > 1:
            decompositions.append(from_record(weights, bands, record))
    # A brand keeps its depths in campaign order; a brand missing a campaign stays in the mapping
    # with fewer than three, and `brands_complete` is what the figures are reported over.
    ordered = {
        brand: [by_pos[k] for k in sorted(by_pos)] for brand, by_pos in sorted(depths.items())
    }
    return DepthResult(ordered, attribute(decompositions))


# ── scheduling defects: every rate limit that ever binds ──────────────────────────────────────

# Owner, 7 November 2026 (Amendment 3): a rate limit INVALIDATES the run like any infrastructure
# kind, AND every occurrence is a SCHEDULING DEFECT to be told about. Baseline B makes more calls
# than the Proposed arm, so if rate limits ever bind they bind unevenly across arms and would
# depress the arm that happened to make more calls. The scheduler must pace so this is always empty.
# A WAIT is not a defect (free-tier pacing is expected and recorded per call); a call that ended
# `rate_limited` exhausted its waits, and that is the defect.
SCHEDULING_DEFECTS_SQL = """
select r.arm, l.run_id, l.created_at, l.agent_id
from public.llm_calls l
join public.runs r on r.id = l.run_id and r.org_id = l.org_id
join public.organisations o on o.id = l.org_id
where l.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and l.outcome = 'rate_limited'
order by l.created_at
"""


@dataclass(frozen=True, slots=True)
class SchedulingDefect:
    arm: str
    run_id: UUID
    at: datetime
    agent_id: str | None


def invalidation_lines(found: Sequence[Invalidation]) -> list[str]:
    """Every invalidation, with the outage window it overlapped and the run that replaced it
    (`reruns.invalidations`). Printed including when empty, and an invalidation with NO recorded
    window is called out: either the outage record is incomplete or the classification fired for
    another reason, and both are things to look at rather than pass over."""
    if not found:
        return ["INVALIDATED RUNS (re-run, in no figure): none"]
    out = [f"INVALIDATED RUNS (re-run, in no figure): {len(found)}"]
    for i in found:
        replaced_by = "NO RE-RUN RECORDED" if i.rerun_run_id is None else f"re-run {i.rerun_run_id}"
        window = (
            f"{len(i.windows)} outage window(s)"
            if i.window_found
            else "NO OUTAGE WINDOW OVERLAPPED: look at this"
        )
        agents = ", ".join(str(g.get("agent")) for g in i.gaps) or "none recorded"
        out.append(
            f"  {i.run_id} {i.infrastructure_failure} ({i.kind}; agents {agents}): "
            f"{window}; {replaced_by}"
        )
    return out


def scheduling_defect_lines(defects: Sequence[SchedulingDefect]) -> list[str]:
    """Always printed, including when empty: "none" is the result the scheduler is aiming at."""
    if not defects:
        return ["SCHEDULING DEFECTS (rate limits that bound): none"]
    per_arm: dict[str, int] = {}
    for d in defects:
        per_arm[d.arm] = per_arm.get(d.arm, 0) + 1
    out = [
        f"SCHEDULING DEFECTS (rate limits that bound): {len(defects)}, and they are UNEVEN ACROSS "
        f"ARMS by construction: {per_arm}",
        "  Each invalidated its run and cost a re-run. Baseline B makes the most calls, so a "
        "binding rate limit depresses whichever arm was running: pace the schedule, do not "
        "reinterpret the figures (owner, 2026-11-07).",
    ]
    out += [f"  {d.at.isoformat()} {d.arm} run {d.run_id} agent {d.agent_id}" for d in defects]
    return out


# The invalidated runs, with the run that replaced each: `supersedes_run_id` (migration 0048) read
# from the REPLACEMENT's side, so the link is the one the backend wrote at insert, not a guess.
INVALIDATED_RUNS_SQL = """
select r.id, r.created_at, coalesce(r.completed_at, now()), r.failure,
       (select s.id from public.runs s where s.supersedes_run_id = r.id)
from public.runs r
join public.organisations o on o.id = r.org_id
where r.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and r.failure is not null
order by r.created_at
"""


async def evaluation_invalidations(
    conn: AsyncConnection, org_ids: Collection[UUID]
) -> list[Invalidation]:
    """Every invalidated run with the outage window it overlapped and the run that replaced it.

    Built on 7 November 2026 (decision-log 2.53 item 6): `reruns.invalidations` was written on
    5 November and called by nothing, so an invalidation would have been re-run and never
    reported. The outage windows are derived from the call log, over each run's ACTIVE time."""
    windows, _touched = await evaluation_outages(conn, list(org_ids))
    cur = await conn.execute(INVALIDATED_RUNS_SQL, {"org_ids": list(org_ids)})
    rows = [(r[0], r[1], r[2], r[3], r[4]) for r in await cur.fetchall()]
    return list_invalidations(rows, windows)


async def scheduling_defects(
    conn: AsyncConnection, org_ids: Collection[UUID]
) -> list[SchedulingDefect]:
    cur = await conn.execute(SCHEDULING_DEFECTS_SQL, {"org_ids": list(org_ids)})
    return [
        SchedulingDefect(arm, run_id, at, agent) for arm, run_id, at, agent in await cur.fetchall()
    ]


# ── the report ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Report:
    paired: Sequence[PairedRequest]
    incomplete: Sequence[str]
    excluded: Sequence[Excluded]
    linkage_disagreements: Sequence[str]
    sensitivities: Mapping[str, Sensitivity]  # arm -> its figures, twice
    positions: Mapping[str, Mapping[int, Efficiency]]
    depth: DepthResult
    defects: Sequence[SchedulingDefect] = ()
    invalidations: Sequence[Invalidation] = ()
    quality_sample: str = (
        "Human quality rating uses a random subset of 15 requests, 45 campaigns per rater "
        "(decision-log 2.18); it is declared wherever a quality result is reported."
    )

    def lines(self) -> list[str]:
        out = [
            f"PAIRED REQUESTS: {len(self.paired)} (every arm's primary attempt present)",
        ]
        if self.incomplete:
            out += ["NOT PAIRED, and in no figure:", *[f"  {x}" for x in self.incomplete]]
        if self.excluded:
            out.append(f"RUNS EXCLUDED as not the primary attempt: {len(self.excluded)}")
            out += [f"  {e.request_id}:{e.arm} {e.run_id}: {e.why}" for e in self.excluded]
        if self.linkage_disagreements:
            out += [
                "LINKAGE DISAGREEMENTS (runs.supersedes_run_id against the attempt rule):",
                *[f"  {x}" for x in self.linkage_disagreements],
            ]
        for arm, sens in self.sensitivities.items():
            out += [f"── {arm} ──", *sens.lines()]
            out.append("by campaign position:")
            for position, figures in self.positions[arm].items():
                out += [f"  position {position}:", *[f"    {x}" for x in figures.lines()]]
        out += ["── the engine ──", *self.depth.lines()]
        out += ["── infrastructure ──", *invalidation_lines(self.invalidations)]
        out += ["── scheduling ──", *scheduling_defect_lines(self.defects)]
        out += ["── sample ──", f"  {self.quality_sample}"]
        return out


def build_report(
    runs: Iterable[RunRecord],
    *,
    bands: Bands,
    weights: Mapping[Any, float],
    unit_costs: UnitCosts | None = None,
    arms: Sequence[str] = ("proposed", "baseline_a"),
    baseline_arm: str = BASELINE,
    defects: Sequence[SchedulingDefect] = (),
    invalidations: Sequence[Invalidation] = (),
) -> Report:
    """Everything, from the run records: selection, pairing, the four figures twice, by position,
    and the depth pair with its attribution."""
    selection = select_primary(runs)
    paired, incomplete = pair_requests(selection)
    return Report(
        paired=paired,
        incomplete=incomplete,
        excluded=selection.excluded,
        linkage_disagreements=selection.linkage_disagreements,
        sensitivities={
            arm: sensitivity(paired, arm, baseline_arm=baseline_arm, unit_costs=unit_costs)
            for arm in arms
        },
        positions={
            arm: by_position(paired, arm, baseline_arm=baseline_arm, unit_costs=unit_costs)
            for arm in arms
        },
        depth=depth_result(paired, bands, weights),
        defects=defects,
        invalidations=invalidations,
    )


# ── the read ──────────────────────────────────────────────────────────────────────────────────

# Evaluation organisations only, like every other evaluation read (Rules.md P3), practice and test
# organisations excluded by flag.
RUN_RECORDS_SQL = """
select r.id, r.arm, r.request ->> 'brief', r.status, r.failure, r.created_at,
       r.search_count, r.pages_fetched, r.depth, r.timings,
       c.strategy -> 'necessity',
       exists (select 1 from public.runs s where s.supersedes_run_id = r.id) as superseded,
       r.supersedes_run_id,
       exists (select 1 from public.llm_calls l
               where l.run_id = r.id and l.outcome = 'provider_unavailable') as provider_retry
from public.runs r
join public.organisations o on o.id = r.org_id
left join public.campaigns c on c.run_id = r.id and c.org_id = r.org_id
where r.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
order by r.created_at, r.id
"""


async def run_records(conn: AsyncConnection, org_ids: Collection[UUID]) -> list[RunRecord]:
    """Every evaluation run, with its work calls and cost from the attempt-aware read.

    A run whose brief is not the test set's is REFUSED, not skipped: the request id is the only
    link between a run and the design (the run row carries no request id, a finding recorded in
    decision-log 2.53), so a brief nobody registered means the scope is wrong."""
    by_brief = {inp.brief: rid for rid, inp in request_inputs().items()}
    index = request_index()
    calls: list[CallRow] = await llm_calls_by_attempt(conn, list(org_ids))
    per_run: dict[UUID, list[CallRow]] = {}
    for call in calls:
        per_run.setdefault(call.run_id, []).append(call)
    cur = await conn.execute(RUN_RECORDS_SQL, {"org_ids": list(org_ids)})
    records: list[RunRecord] = []
    for row in await cur.fetchall():
        (
            run_id, arm, brief, status, failure, created_at, searches, pages, depth, timings,
            necessity, superseded, supersedes, provider_retry,
        ) = row  # fmt: skip
        request_id = by_brief.get(brief)
        if request_id is None:
            raise ValueError(
                f"run {run_id} has a brief that is in no registered request: {str(brief)[:60]!r}"
            )
        meta = index[request_id]
        run_calls = per_run.get(run_id, [])
        work = totals(run_calls)
        records.append(
            RunRecord(
                run_id=run_id,
                arm=arm,
                request_id=request_id,
                position=meta.position,
                brand_id=meta.brand_id,
                status=status,
                failure=failure,
                created_at=created_at,
                searches=searches,
                pages_fetched=pages,
                depth=depth,
                timings=timings,
                necessity=necessity,
                superseded=superseded,
                supersedes_run_id=supersedes,
                provider_retry=provider_retry,
                work_calls=work.work_calls,
                work_cost_usd=work.work_cost_usd,
                model_ms=sum(
                    c.latency_ms or 0
                    for c in run_calls
                    if c.last_attempt and c.outcome not in ("orphaned", "provider_unavailable")
                ),
            )
        )
    return records
