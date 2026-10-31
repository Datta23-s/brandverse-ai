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
    evidence_item_confidence: float = Field(gt=0, lt=1)
    typical_freshness_days: dict[str, int]


class Retrieval(_Strict):
    hnsw_ef_search: int = Field(ge=1, le=1000)  # pgvector's accepted range
    # The knowledge slots `memory_coverage` counts (Memory.md section 3.2): the fraction of these
    # filled at M >= the adapt band. PROVISIONAL: the Research Necessity Engine (not built) owns it.
    required_slots: tuple[
        Literal["persona", "competitor", "insight", "claim", "creative", "sector"], ...
    ] = Field(min_length=1)


class Research(_Strict):
    search_cache_ttl_s: int = Field(ge=1)  # a parameter of the results, never a constant
    # A search answered while some engines failed is a transient failure: it is cached, marked
    # degraded, for this (much shorter) time instead. It must not exceed the clean TTL.
    search_cache_degraded_ttl_s: int = Field(ge=1)
    # The search engines the search configuration is declared to be, in advance and reported beside
    # every result; "degraded" means one of THESE failed. None: not declared yet (the rate probe
    # decides it, docs/evaluation-plan.md section 8), so every engine counts. A required key: it is
    # written as null on purpose, not left out.
    declared_engines: list[str] | None
    # The query planner (app/research/planner.py). Parameters of the results, reported beside them.
    # 4 facets x this many = q. 2 is the smallest that makes depth a variable (at 1, D1 and D2 are
    # the same list); NOT set from the search rate. A required key.
    planner_queries_per_facet: int = Field(ge=1, le=2)
    planner_locale: str = Field(min_length=1)
    # The most pages fetched for one query: a SAFETY BOUND (100, untuned), not a design parameter
    # (docs/fetcher.md, owner 2026-10-28). None would mean no bound. Reported beside every result.
    max_pages_per_query: int | None
    # Retries of a transient page failure while capturing the evaluation snapshot; bounded, untuned.
    snapshot_page_retries: int = Field(ge=0, le=10)
    # The recorder's session loop (app/research/recorder.py). Untuned defaults, reported with every
    # session; seconds unless named otherwise.
    recorder_search_gap_s: float = Field(gt=0)
    recorder_gap_ceiling_s: float = Field(gt=0)
    recorder_gap_increase_factor: float = Field(ge=1)
    recorder_gap_decrease_step_s: float = Field(ge=0)
    recorder_clean_run_to_speed_up: int = Field(ge=1)
    recorder_backoff_base_s: float = Field(gt=0)
    recorder_backoff_max_s: float = Field(gt=0)
    recorder_backoff_jitter: float = Field(ge=0, le=1)
    recorder_dark_ceiling_s: float = Field(gt=0)
    recorder_wall_limit_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _recorder_bounds_are_ordered(self) -> "Research":
        if self.recorder_gap_ceiling_s < self.recorder_search_gap_s:
            raise ValueError("recorder_gap_ceiling_s must not be below recorder_search_gap_s")
        if self.recorder_backoff_max_s < self.recorder_backoff_base_s:
            raise ValueError("recorder_backoff_max_s must not be below recorder_backoff_base_s")
        return self

    extraction_min_chars: int = Field(ge=1)
    extraction_max_chars: int = Field(ge=1)
    # The page fetcher (app/research/fetch.py). Every one is a parameter of the results.
    page_cache_ttl_s: int = Field(ge=1)  # how long a fetched page body may live in Redis
    fetch_timeout_s: float = Field(gt=0)  # per network operation (connect, each read)
    fetch_total_timeout_s: float = Field(gt=0)  # the whole fetch, redirects included
    fetch_max_bytes: int = Field(ge=1)  # a longer body is refused, not truncated
    fetch_max_redirects: int = Field(ge=0)
    fetch_user_agent: str = Field(min_length=1)
    # Politeness: the least time between two requests this fetcher starts to the SAME host (page,
    # robots.txt and redirect hops alike). 0 turns it off. A property of how the fetcher behaves
    # toward a server, not a planning parameter; reported like every number here.
    fetch_host_delay_s: float = Field(ge=0)
    robots_ttl_s: int = Field(ge=1)  # how long one origin's robots.txt is kept (RFC 9309: <= 24 h)
    robots_max_bytes: int = Field(ge=1)  # only this much of a robots.txt is read (RFC: >= 500 KiB)

    @model_validator(mode="after")
    def _degraded_cache_is_shorter(self) -> "Research":
        if self.search_cache_degraded_ttl_s > self.search_cache_ttl_s:
            raise ValueError("search_cache_degraded_ttl_s must not exceed search_cache_ttl_s")
        return self

    @model_validator(mode="after")
    def _declared_engines_are_a_real_set(self) -> "Research":
        if self.declared_engines is None:
            return self
        names = [e.strip().casefold() for e in self.declared_engines]
        if not names or "" in names or len(set(names)) != len(names):
            raise ValueError("declared_engines must be null or a non-empty list of distinct names")
        return self


class SourceWeights(_Strict):
    authority: float = Field(ge=0)
    recency: float = Field(ge=0)
    specificity: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "SourceWeights":
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-9:
            raise ValueError("source-score weights must sum to 1")
        return self


class AuthorityClass(_Strict):
    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    suffixes: list[str] = Field(min_length=1)


class Authority(_Strict):
    unknown: float = Field(ge=0, le=1)
    classes: list[AuthorityClass]

    @model_validator(mode="after")
    def _no_suffix_in_two_classes(self) -> "Authority":
        seen: set[str] = set()
        for c in self.classes:
            for suffix in c.suffixes:
                if suffix != suffix.casefold().strip(".") or suffix in seen:
                    raise ValueError(
                        f"authority suffix {suffix!r} is not lower-case bare or repeats"
                    )
                seen.add(suffix)
        return self


class SourceScoring(_Strict):
    weights: SourceWeights
    authority: Authority
    recency_half_life_days: float = Field(gt=0)
    specificity_min_word_chars: int = Field(ge=1)
    specificity_ignore_words: list[str]
    accept_at_or_above: float = Field(ge=0, le=1)


class ScoringConfig(_Strict):
    version: str
    memory: MemoryScoring
    writeback: Writeback
    retrieval: Retrieval
    research: Research
    source: SourceScoring


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
