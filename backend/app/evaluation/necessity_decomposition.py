"""Memory versus novelty: what produced a depth reduction (owner, 2026-11-06; decision-log 2.45).

The three-arm design does not separate "memory reduced the work" from "the request was less novel
the second time": Baseline B has no memory and runs at a fixed depth, so nothing in the arms does.
This decomposition does, from the R inputs the engine already logs, at zero call cost.

For one campaign, R = (wN N + wU U + wS S) / (wN + wU + wS), so each input's CONTRIBUTION to R is
its weighted share. The COUNTERFACTUAL depth holds U at 1.00 (an empty memory) with the campaign's
own N and S: the depth novelty alone would have produced. Depth reduction is counted in LEVELS below
D2 (D2 -> D1 is one, D2 -> D0 is two):

    reduction levels  = rank(D2)            - rank(chosen depth)
    novelty levels    = rank(D2)            - rank(counterfactual depth at U = 1)
    memory levels     = rank(counterfactual) - rank(chosen depth)    (reduction = novelty + memory)

The share of reduction attributable to accumulated memory is memory levels over reduction levels,
summed over the campaigns that were reduced. It is a statement about what DROVE THE ENGINE'S DEPTH,
not about whether the research actually needed was smaller.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection

Depth = Literal["D0", "D1", "D2"]
RANK: dict[str, int] = {"D0": 0, "D1": 1, "D2": 2}

# Evaluation organisations only, like every other evaluation read. The engine's record travels with
# the campaign's strategy (nodes.build_strategy).
NECESSITY_RECORDS_SQL = """
select r.id, r.arm, c.strategy -> 'necessity'
from public.campaigns c
join public.runs r on r.id = c.run_id and r.org_id = c.org_id
join public.organisations o on o.id = c.org_id
where c.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and r.arm = 'proposed'
  and c.strategy ? 'necessity'
  and c.strategy -> 'necessity' <> 'null'::jsonb
  and c.strategy -> 'necessity' ? 'novelty'
  -- primary attempts only: a run an infrastructure failure replaced is not a result (0048)
  and not exists (select 1 from public.runs s where s.supersedes_run_id = r.id)
order by r.created_at
"""


@dataclass(frozen=True, slots=True)
class Bands:
    d0_below: float
    d2_from: float

    def depth(self, r: float) -> Depth:
        return "D0" if r < self.d0_below else "D2" if r >= self.d2_from else "D1"


@dataclass(frozen=True, slots=True)
class Decomposition:
    r: float
    n_part: float  # N's contribution to R
    u_part: float  # U's contribution to R
    s_part: float  # S's contribution to R
    depth: Depth
    counterfactual_depth: Depth  # the depth with U held at 1.00: novelty (and sector) alone
    reduction_levels: int
    novelty_levels: int
    memory_levels: int


def decompose(
    weights: Mapping[Any, float], bands: Bands, *, novelty: float, u: float, s_sector: float
) -> Decomposition:
    total = weights["N"] + weights["U"] + weights["S"]
    n_part = weights["N"] * novelty / total
    u_part = weights["U"] * u / total
    s_part = weights["S"] * s_sector / total
    r = n_part + u_part + s_part
    depth = bands.depth(r)
    cf = bands.depth(n_part + weights["U"] * 1.0 / total + s_part)
    return Decomposition(
        r=r,
        n_part=n_part,
        u_part=u_part,
        s_part=s_part,
        depth=depth,
        counterfactual_depth=cf,
        reduction_levels=RANK["D2"] - RANK[depth],
        novelty_levels=RANK["D2"] - RANK[cf],
        memory_levels=RANK[cf] - RANK[depth],
    )


@dataclass(frozen=True, slots=True)
class Attribution:
    campaigns: int
    reduced: int  # campaigns that ran below D2
    reduction_levels: int
    memory_levels: int
    novelty_levels: int

    @property
    def memory_share(self) -> float | None:
        """Share of the depth reduction attributable to accumulated memory; None if no reduction."""
        return None if self.reduction_levels == 0 else self.memory_levels / self.reduction_levels

    def line(self) -> str:
        share = "n/a (no reduction)" if self.memory_share is None else f"{self.memory_share:.0%}"
        return (
            f"{self.reduced} of {self.campaigns} campaigns reduced; "
            f"{self.reduction_levels} levels, of which memory {self.memory_levels} and "
            f"novelty {self.novelty_levels}: memory share {share}"
        )


def attribute(decompositions: Iterable[Decomposition]) -> Attribution:
    ds = list(decompositions)
    return Attribution(
        campaigns=len(ds),
        reduced=sum(1 for d in ds if d.reduction_levels > 0),
        reduction_levels=sum(d.reduction_levels for d in ds),
        memory_levels=sum(d.memory_levels for d in ds),
        novelty_levels=sum(d.novelty_levels for d in ds),
    )


def from_record(
    weights: Mapping[Any, float], bands: Bands, record: Mapping[str, Any]
) -> Decomposition:
    """A decomposition from the engine's logged record (`NecessityDecision.record()`)."""
    return decompose(
        weights,
        bands,
        novelty=float(record["novelty"]),
        u=float(record["u"]),
        s_sector=float(record["s_sector"]),
    )


async def necessity_records(
    conn: AsyncConnection, org_ids: Collection[UUID]
) -> list[tuple[UUID, str, dict[str, Any]]]:
    """(run id, arm, the engine's record) for every PROPOSED campaign that carries one, primary
    attempts only. Baseline campaigns carry `{policy, depth, requested}` without N, U or S, which
    `from_record` cannot read (it raised KeyError before this filter); a superseded attempt is not
    a result."""
    cur = await conn.execute(NECESSITY_RECORDS_SQL, {"org_ids": list(org_ids)})
    return [(run_id, arm, rec) async for run_id, arm, rec in cur]
