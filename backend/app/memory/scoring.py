"""The M-score and the reuse bands (Memory.md §3; config/scoring.yaml).

`app.match_memory` (migration 0022) returns the candidate set; this module scores each candidate
for the request at hand, because P, C and A need context the database does not hold.

    M = Σ wᵢ·xᵢ / Σ wᵢ   over the components available

| | Component | Here |
|---|---|---|
| S | semantic similarity | cosine of query and item embeddings, clamped to [0, 1] |
| P | product match | 1.0 same product, 0.7 same brand, 0.4 same category, 0.1 otherwise |
| C | customer match | supplied per candidate when persona vectors exist (Phase 6); else left out |
| A | sector alignment | 1.0 same sector, 0.6 adjacent (declared pairs), 0.2 unrelated or unknown |
| F | freshness | exp(−λ·age/freshness_days); a verified claim is exempt (F = 1) |
| E | evidence quality | source score × confidence, when a source score exists (Phase 5); else out |

A component that cannot be computed is left out and the remaining weights renormalised — never
filled with a guess — and the breakdown lists what was left out, so a reported M always says what
it was made of. Freshness is applied at scoring time, never at write time (Memory.md §10).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import REPO_ROOT

SCORING_PATH: Path = REPO_ROOT / "config" / "scoring.yaml"
Component = Literal["S", "P", "C", "A", "F", "E"]
Band = Literal["reuse", "adapt", "support", "fresh"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Weights(_Strict):
    S: float = Field(ge=0)
    P: float = Field(ge=0)
    C: float = Field(ge=0)
    A: float = Field(ge=0)
    F: float = Field(ge=0)
    E: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "Weights":
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-9:
            raise ValueError("M-score weights must sum to 1")
        return self


class ProductMatch(_Strict):
    same_product: float
    same_brand: float
    same_category: float
    other: float


class SectorAlignment(_Strict):
    same: float
    adjacent: float
    unrelated: float


class Bands(_Strict):
    reuse: float
    adapt: float
    support: float

    @model_validator(mode="after")
    def _ordered(self) -> "Bands":
        if not 0 < self.support < self.adapt < self.reuse <= 1:
            raise ValueError("bands must satisfy 0 < support < adapt < reuse <= 1")
        return self


class MemoryScoring(_Strict):
    weights: Weights
    product_match: ProductMatch
    sector_alignment: SectorAlignment
    adjacent_sectors: list[tuple[str, str]] = Field(default_factory=list)
    freshness_lambda: float = Field(gt=0)
    default_freshness_days: dict[str, int]
    bands: Bands


class Writeback(_Strict):
    near_duplicate_cosine: float = Field(gt=0, lt=1)
    reuse_confidence_step: float = Field(gt=0, lt=1)
    confidence_cap: float = Field(gt=0, le=1)
    correction_step: float = Field(gt=0, lt=1)
    revoke_below: float = Field(gt=0, lt=1)
    max_item_tokens: int = Field(gt=0)


class Retrieval(_Strict):
    hnsw_ef_search: int = Field(ge=1, le=1000)  # pgvector's accepted range


class Research(_Strict):
    search_cache_ttl_s: int = Field(ge=1)  # a parameter of the results, never a constant


class ScoringConfig(_Strict):
    version: str
    memory: MemoryScoring
    writeback: Writeback
    retrieval: Retrieval
    research: Research


def load_scoring(path: Path = SCORING_PATH) -> ScoringConfig:
    return ScoringConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class Candidate:
    """One row of app.match_memory, as the scorer needs it."""

    id: str
    kind: str
    brand_id: str | None
    product_id: str | None
    product_category: str | None
    claim_type: str | None
    sector: str | None
    confidence: float | None
    created_at: datetime
    freshness_days: int | None
    similarity: float | None
    customer_similarity: float | None = None  # C, when persona vectors exist (Phase 6)
    source_score: float | None = None  # for E, when source scoring exists (Phase 5)


@dataclass(frozen=True, slots=True)
class RequestContext:
    product_id: str | None
    brand_id: str | None
    product_category: str | None
    sector: str | None
    now: datetime


@dataclass(frozen=True, slots=True)
class Score:
    m: float
    band: Band
    components: Mapping[Component, float]
    left_out: tuple[Component, ...] = field(default=())


def band(m: float, bands: Bands) -> Band:
    if m >= bands.reuse:
        return "reuse"
    if m >= bands.adapt:
        return "adapt"
    if m >= bands.support:
        return "support"
    return "fresh"


def freshness(c: Candidate, ctx: RequestContext, cfg: MemoryScoring) -> float:
    if c.kind == "claim" and c.claim_type == "verified":
        return 1.0  # a verified spec does not become false because it is old (Memory.md §3.3)
    days = c.freshness_days or cfg.default_freshness_days.get(c.kind)
    if not days:
        raise ValueError(f"no freshness_days for kind {c.kind!r}")
    age = max(0.0, (ctx.now - c.created_at).total_seconds() / 86400)
    return math.exp(-cfg.freshness_lambda * age / days)


def product_match(c: Candidate, ctx: RequestContext, cfg: MemoryScoring) -> float:
    p = cfg.product_match
    if ctx.product_id is not None and c.product_id == ctx.product_id:
        return p.same_product
    if ctx.brand_id is not None and c.brand_id == ctx.brand_id:
        return p.same_brand
    if ctx.product_category is not None and c.product_category == ctx.product_category:
        return p.same_category
    return p.other


def sector_alignment(c: Candidate, ctx: RequestContext, cfg: MemoryScoring) -> float:
    a = cfg.sector_alignment
    if c.sector is None or ctx.sector is None:
        return a.unrelated  # unknown alignment is not rewarded
    if c.sector == ctx.sector:
        return a.same
    pairs = {frozenset(p) for p in cfg.adjacent_sectors}
    return a.adjacent if frozenset((c.sector, ctx.sector)) in pairs else a.unrelated


def score(c: Candidate, ctx: RequestContext, cfg: MemoryScoring) -> Score:
    values: dict[Component, float | None] = {
        "S": None if c.similarity is None else min(1.0, max(0.0, c.similarity)),
        "P": product_match(c, ctx, cfg),
        "C": c.customer_similarity,
        "A": sector_alignment(c, ctx, cfg),
        "F": freshness(c, ctx, cfg),
        "E": (
            None
            if c.source_score is None or c.confidence is None
            else c.source_score * c.confidence
        ),
    }
    weights = cfg.weights.model_dump()
    present = {k: v for k, v in values.items() if v is not None}
    total = sum(weights[k] for k in present)
    if total == 0:
        raise ValueError("no M-score component could be computed")
    m = sum(weights[k] * v for k, v in present.items()) / total
    left_out = tuple(k for k, v in values.items() if v is None)
    return Score(m, band(m, cfg.bands), present, left_out)


def memory_coverage(best_by_slot: Mapping[str, Sequence[float]], cfg: MemoryScoring) -> float:
    """Fraction of required knowledge slots filled from memory at M ≥ the adapt band (0.80)."""
    if not best_by_slot:
        return 0.0
    filled = sum(1 for ms in best_by_slot.values() if ms and max(ms) >= cfg.bands.adapt)
    return filled / len(best_by_slot)
