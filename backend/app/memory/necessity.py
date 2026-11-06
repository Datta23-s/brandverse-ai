"""The Research Necessity Engine (Architecture.md 6.1; decision-log 2.18, 2.21, 2.35).

Pure: it takes the three inputs and returns the decision. Every parameter is the FROZEN one in
`config/memory_scoring.yaml` (`Necessity`), pinned by tests/test_necessity_frozen.py; nothing here
is a constant of its own.

    R = (0.30 N + 0.25 U + 0.15 S) / 0.70          F and E are dropped (decision-log 2.18)
    N = 1 - max cosine to the organisation's earlier requests   (1 with none)    [N-a]
    U = 1 - min(1, n / item_cap), n = recalled items whose S reaches the floor     [quantity]
    S = 1 for a regulated sector, else 0
    D0 below d0_below, D2 from d2_from, D1 between.

The record it returns is what the run keeps: R, each input, n, the depth chosen and the depth
that was requested, so "the second run picked a lower depth" is read from the log.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.memory.scoring import Necessity, necessity_depth, necessity_r, quantity_coverage

Depth = Literal["D0", "D1", "D2"]
DepthPolicy = Literal["requested", "necessity"]


def request_text(
    category: str | None, sector: str | None, brief: str | None, audience: str | None
) -> str:
    """What is embedded for N: the same four fields, in the same form, the stage-2 measurements
    used (scripts/phase7_stage2.py), so the live engine and the registered measurement agree."""
    return f"{category or ''}. {sector or ''}. {brief or ''} {audience or ''}"


def novelty_against(vector: Sequence[float], prior_vectors: Sequence[Sequence[float]]) -> float:
    """1 - the largest cosine to an earlier request (the encoder's vectors are unit length, so the
    dot product is the cosine); 1.0 when there is none, clamped to [0, 1]."""
    if not prior_vectors:
        return 1.0
    best = max(sum(a * b for a, b in zip(vector, p, strict=True)) for p in prior_vectors)
    return min(1.0, max(0.0, 1.0 - float(best)))


@dataclass(frozen=True, slots=True)
class NecessityDecision:
    novelty: float
    n_recalled: int
    n_relevant: int  # n: recalled items at or above the relevance floor, capped at item_cap
    u: float
    s_sector: float
    r: float
    advised: Depth  # what R says
    requested: Depth  # what the request asked for
    depth: Depth  # what the run does: `advised` under the necessity policy, else `requested`
    policy: DepthPolicy

    def record(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "r": round(self.r, 4),
            "novelty": round(self.novelty, 4),
            "u": round(self.u, 4),
            "s_sector": self.s_sector,
            "n_recalled": self.n_recalled,
            "n_relevant": self.n_relevant,
            "advised": self.advised,
            "requested": self.requested,
            "depth": self.depth,
        }


def decide(
    cfg: Necessity,
    *,
    novelty: float,
    similarities: Sequence[float],
    regulated: bool,
    requested: Depth,
    policy: DepthPolicy,
) -> NecessityDecision:
    n, coverage = quantity_coverage(similarities, cfg)
    u = 1.0 - coverage
    s_sector = 1.0 if regulated else 0.0
    r = necessity_r(novelty, u, s_sector, cfg)
    advised = necessity_depth(r, cfg)
    return NecessityDecision(
        novelty=novelty,
        n_recalled=len(similarities),
        n_relevant=n,
        u=u,
        s_sector=s_sector,
        r=r,
        advised=advised,
        requested=requested,
        depth=advised if policy == "necessity" else requested,
        policy=policy,
    )
