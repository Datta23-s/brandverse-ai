"""The real implementations of the graph's ports, over the pieces that already exist.

Nothing here is reached by the graph tests with a live dependency: they pass fakes, or these
adapters over `OfflineSearch` and an in-memory page source. A live run (real search, real pages)
is a separate, deliberate act (Phases.md Phase 6: live runs wait for the recording).

**Findings carry attribution and score, not text.** A checkpoint is a Postgres row written at
every step, and the zero-retention rule keeps page bodies out of Postgres (Rules.md P11). An
extracted page is not a raw body, but it is still third-party prose, so state holds only what a
finding IS (url, title, score, engines, queries, date). How an agent reads the text of the
findings it reasons over is a decision for chunk B, where the first agent that needs it exists.
"""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import GateReport
from app.core.truth import ProductTruth, load_product_truth
from app.memory.scoring import SourceScoring
from app.memory.writeback import MemoryStage, WritebackEncoder, write_memory
from app.orchestrator.deps import LoadContext, ProductContext, Research, ResearchResult, WriteMemory
from app.orchestrator.state import Depth, RunState
from app.research.driver import Mode, research
from app.research.extraction import Extractor
from app.research.fetch import PageSource
from app.research.planner import PlanRequest, plan_queries
from app.research.search import SearchProvider


def pool_context_loader(pool: AsyncConnectionPool[AsyncConnection]) -> LoadContext:
    """The product and its truth, read by the backend's own role and filtered by organisation (the
    worker has no user to act as; the API does the membership check before a run is queued)."""

    async def load(org_id: UUID, product_id: UUID) -> ProductContext | None:
        async with pool.connection() as conn:
            truth = await load_product_truth(conn, org_id, product_id)
            if truth is None:
                return None
            cur = await conn.execute(
                "select id, brand_id, name, category from products where id = %s and org_id = %s",
                (product_id, org_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        product = {"id": str(row[0]), "brand_id": str(row[1]), "name": row[2], "category": row[3]}
        return ProductContext(product, truth)

    return load


def driver_research(
    search: SearchProvider,
    pages: PageSource,
    extractor: Extractor,
    source_cfg: SourceScoring,
    *,
    queries_per_facet: int,
    locale: str,
    max_pages_per_query: int | None,
    today: Callable[[], date],
) -> Research:
    """Step 6 over the Phase 5 research driver, in PRODUCTION mode (a degraded run is partial and
    declares its gap; only the evaluation's recording aborts a request)."""

    async def run(state: RunState, depth: Depth) -> ResearchResult:
        product = state.get("product") or {}
        truth = state.get("truth") or {}
        request = PlanRequest(
            sector=(truth.get("sector") or "general").replace("_", " "),
            regulated=bool(truth.get("regulated")),
            category=product.get("category") or "product",
        )
        plan = plan_queries(
            request,
            queries_per_facet=queries_per_facet,
            locale=locale,
            max_pages_per_query=max_pages_per_query,
        )
        outcome = await research(
            plan,
            depth,
            mode=Mode.PRODUCTION,
            search=search,
            pages=pages,
            extractor=extractor,
            source_cfg=source_cfg,
            today=today(),
        )
        findings = [
            {
                "url": f.page.url,
                "title": f.page.title,
                "score": f.score.value,
                "published": f.page.published.isoformat() if f.page.published else None,
                "queries": list(f.queries),
                "engines": list(f.engines),
                "first_party": f.first_party,
            }
            for f in outcome.findings
        ]
        report = {
            "depth": depth,
            "searches": outcome.searches_issued,
            "partial": outcome.partial,
            "uncovered_facets": list(outcome.uncovered_facets),
            "pages_fetched": outcome.pages_fetched,
            "pages_served": outcome.pages_served,
            "discarded_low_score": outcome.discarded_low_score,
        }
        return ResearchResult(findings, report)

    return run


def writeback_step(
    pool: AsyncConnectionPool[AsyncConnection], encoder: WritebackEncoder
) -> WriteMemory:
    """Step 15 over `writeback.write_memory`. The candidates are EMPTY for now: reading the facts
    out of an approved campaign is not built, so a run stages no new item. What does run is the
    real approval check: an unapproved campaign stages nothing that adds trust."""

    async def step(
        state: RunState, stage: MemoryStage, report: GateReport, truth: ProductTruth
    ) -> dict[str, Any]:
        approved = (state.get("approval") or {}).get("decision") == "approve"
        product = state.get("product") or {}
        screening = await write_memory(
            pool,
            stage,
            run_id=UUID(state["run_id"]),
            org_id=UUID(state["org_id"]),
            brand_id=UUID(product["brand_id"]),
            product_id=UUID(state["product_id"]),
            approved=approved,
            candidates=[],
            reused=[],
            report=report,
            truth=truth,
            encoder=encoder,
        )
        return {"approved": approved, "staged": len(stage.items), "refused": len(screening.refused)}

    return step
