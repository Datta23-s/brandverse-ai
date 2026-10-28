"""The query planner (docs/evaluation-plan.md section 8, Architecture.md section 6).

One request in, ONE ranked query list out. It runs once per request, before any arm picks a depth,
from the request alone; D0 takes none of the list, D1 the first pass, D2 all of it. Every arm that
researches draws from this one list, so the evidence available to an arm is identical and only how
much of it an arm consumes varies.

**Query generation is NOT adaptive.** The queries are filled from fixed templates and depend on the
request's fields and nothing else: not on memory, not on earlier results, not on the arm. The
adaptivity this project studies lives in depth selection and memory reuse, not here. This is stated
because a thesis about adaptive intelligence would otherwise leave a panel to notice that its query
generator is a lookup.

**Facets.** Each is a distinct information need with a named consumer (a facet exists because
something downstream uses it): competitor and category positioning (A2), audience needs and
objections (A3), independent reference facts for the product's attributes (A1, A7), and sector rules
and advertising norms (sector risk, A7). More results on one facet never cover another, so the list
is built to cover facets first.

**Ranking = breadth-first interleave.** Pass 1 is the primary query of every facet, in the facet
priority order; pass 2 is every facet's second-angle query in the same order. Consequences: D1 (the
first pass) covers every facet exactly once; a shorter prefix provably leaves a facet uncovered
(`QueryPlan.uncovered`); and if the search budget ever forces cuts, the tail goes first (the second
query of the lowest-priority facet), so the list degrades by depth and never by dropping a facet.
The facet priority order is untuned developer judgement (Rules.md A5): a regulated sector puts the
sector-rules facet first because that is where output is blocked and where an efficacy claim needs
independent evidence (V6); otherwise competitor positioning leads.

**Category terms only.** The evaluation's brands and products are pseudonyms, which no search engine
can resolve, so the templates take the product's generic CATEGORY, never a brand or product name.
That is forced, and it has a cost larger than a note: the searches can only return evidence about
the category, never about the specific product (V7, docs/evaluation-plan.md). The objective is
carried on the plan but not used in query text: using it would need keyword extraction from free
text.

Each query is a `PlannedQuery`; the plan reports `template_version`, `queries_per_facet` and the
fetch cap (`max_pages_per_query`, a safety bound of 100, untuned), because all
three are parameters of the results.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.research.search import normalise_query

TEMPLATE_VERSION = "v1"

Facet = Literal["competitor_positioning", "audience_needs", "reference_facts", "sector_rules"]
Depth = Literal["D0", "D1", "D2"]

# Who uses each facet (a facet without a consumer would not exist).
CONSUMER: dict[Facet, str] = {
    "competitor_positioning": "A2 Market & Advertisement Intelligence",
    "audience_needs": "A3 Customer Behaviour Intelligence",
    "reference_facts": "A1 Product & Brand Intelligence; A7 Trust, Risk & Campaign Evaluation",
    "sector_rules": "sector risk (Research Necessity S); A7 Trust, Risk & Campaign Evaluation",
}

# Facet priority, UNTUNED developer judgement, reported with every result.
PRIORITY: dict[bool, tuple[Facet, ...]] = {
    False: ("competitor_positioning", "audience_needs", "reference_facts", "sector_rules"),
    True: ("sector_rules", "reference_facts", "competitor_positioning", "audience_needs"),
}

# (angle 1, angle 2) per facet. Slots: {category} {audience} {sector} {locale}. With no audience,
# the audience facet's first query uses audience-free wording.
TEMPLATES: dict[Facet, tuple[str, str]] = {
    "competitor_positioning": (
        "best {category} brands {locale}",
        "{category} compared popular models features and claims {locale}",
    ),
    "audience_needs": (
        "what {audience} look for when buying {category}",
        "{category} customer reviews common complaints {locale}",
    ),
    "reference_facts": (
        "{category} independent test review specifications explained",
        "{category} buying guide how to choose key specifications",
    ),
    "sector_rules": (
        "{sector} advertising claims rules {locale}",
        "{category} misleading advertising claims not allowed {locale}",
    ),
}
AUDIENCE_FREE_ANGLE_1 = "what buyers look for when buying {category} {locale}"
REGULATED_SECTOR_ANGLE_1 = "{sector} labelling and health claims regulation {locale}"

_STRIP = re.compile(r"[\"()\[\]{}<>|]")
_SPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Free text made safe to put in a query: no quotes, brackets or pipes (search operators), one
    space between words."""
    return _SPACE.sub(" ", _STRIP.sub(" ", text)).strip()


class PlanRequest(BaseModel):
    """The fields of a request the planner reads. No brand or product NAME is among them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sector: str = Field(min_length=1, max_length=80)  # as words, e.g. "health supplements"
    regulated: bool
    category: str = Field(min_length=1, max_length=80)  # the product's generic category
    audience: str | None = Field(default=None, max_length=200)
    objective: str | None = Field(default=None, max_length=200)  # carried, not used in queries


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    text: str
    facet: Facet
    angle: int  # 1 = primary, 2 = second angle
    rank: int  # 1-based position in the shared list


@dataclass(frozen=True, slots=True)
class QueryPlan:
    request: PlanRequest
    queries: tuple[PlannedQuery, ...]
    facets: tuple[Facet, ...]  # in priority order
    queries_per_facet: int
    max_pages_per_query: int | None  # the fetch cap, a safety bound; None = no bound
    template_version: str = TEMPLATE_VERSION

    @property
    def first_pass_size(self) -> int:
        return len(self.facets)

    def for_depth(self, depth: Depth) -> tuple[PlannedQuery, ...]:
        """D0 none, D1 the first pass (every facet once), D2 all of it."""
        if depth == "D0":
            return ()
        if depth == "D1":
            return self.queries[: self.first_pass_size]
        return self.queries

    def covered(self, prefix_length: int) -> frozenset[Facet]:
        return frozenset(q.facet for q in self.queries[:prefix_length])

    def uncovered(self, prefix_length: int) -> frozenset[Facet]:
        return frozenset(self.facets) - self.covered(prefix_length)

    def report(self) -> dict[str, object]:
        """What is reported beside every result that used this plan."""
        return {
            "template_version": self.template_version,
            "generation": "fixed templates; not adaptive",
            "facet_priority": list(self.facets),
            "queries_per_facet": self.queries_per_facet,
            "queries": len(self.queries),
            "d1_queries": self.first_pass_size,
            "max_pages_per_query": self.max_pages_per_query,
        }


def plan_queries(
    request: PlanRequest,
    *,
    queries_per_facet: int,
    locale: str,
    max_pages_per_query: int | None,
) -> QueryPlan:
    """The one ranked query list for a request (a pure function of its arguments)."""
    if not 1 <= queries_per_facet <= 2:
        raise ValueError(
            f"queries_per_facet must be 1 or 2 (two templates exist), got {queries_per_facet}"
        )
    category = clean(request.category)
    sector = clean(request.sector)
    if not category or not sector:
        raise ValueError("category and sector must contain words")
    audience = clean(request.audience).casefold() if request.audience else ""
    slots = {"category": category, "sector": sector, "audience": audience, "locale": clean(locale)}
    facets = PRIORITY[request.regulated]

    def text_for(facet: Facet, angle: int) -> str:
        template = TEMPLATES[facet][angle - 1]
        if facet == "audience_needs" and angle == 1 and not audience:
            template = AUDIENCE_FREE_ANGLE_1
        if facet == "sector_rules" and angle == 1 and request.regulated:
            template = REGULATED_SECTOR_ANGLE_1
        return _SPACE.sub(" ", template.format(**slots)).strip()

    queries: list[PlannedQuery] = []
    seen: set[str] = set()
    for angle in range(1, queries_per_facet + 1):
        for facet in facets:
            text = text_for(facet, angle)
            key = normalise_query(text)
            if key in seen:  # two facets must not spend a search on the same words
                continue
            seen.add(key)
            queries.append(PlannedQuery(text, facet, angle, len(queries) + 1))
    return QueryPlan(request, tuple(queries), facets, queries_per_facet, max_pages_per_query)
