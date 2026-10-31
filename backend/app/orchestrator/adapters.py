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

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import AgentContext
from app.core.claim_classifier import extract_statements
from app.core.gates import GateReport, Statement, record_gate_block
from app.core.truth import ProductTruth, load_product_truth
from app.errors import ExtractionFailed, FetchError
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier
from app.memory.embeddings import Encoder
from app.memory.recall import recall
from app.memory.scoring import ScoringConfig, SourceScoring
from app.memory.writeback import MemoryStage, PersonaVector, WritebackEncoder, write_memory
from app.orchestrator.deps import (
    CheckBrief,
    ExtractStatements,
    LoadContext,
    MemoryResult,
    ProductContext,
    RecordBlock,
    Research,
    ResearchResult,
    Retrieve,
    WriteMemory,
)
from app.orchestrator.state import Depth, RunState
from app.research.driver import Mode, research
from app.research.extraction import Extractor
from app.research.fetch import PageSource
from app.research.planner import PlanRequest, plan_queries
from app.research.search import SearchProvider


class PageEvidence:
    """How an agent reads the text of a finding (`app.agents.base.EvidenceReader`): fetch the page
    from the run's page source and extract it, in memory, every time it is asked for. Nothing is
    kept: state holds the finding's url, and the text comes from here.

    Under REPLAY the page source is `SnapshotPages`: the bytes recorded for the request, the same
    for every arm, and a page that was never recorded raises `SnapshotMiss` (not a FetchError, so
    it is not swallowed here): a replay that went live would silently end the held-constant
    guarantee.
    A page the snapshot froze as a failure, or that extracts to nothing, reads as None, as it did
    for the run that recorded it."""

    def __init__(self, pages: PageSource, extractor: Extractor) -> None:
        self._pages, self._extractor = pages, extractor

    async def text(self, url: str) -> str | None:
        try:
            page = await self._pages.fetch(url)
        except FetchError:
            return None
        try:
            return (
                await asyncio.to_thread(self._extractor.extract, page.html, page.final_url)
            ).text
        except ExtractionFailed:
            return None


def classifier_extractor(gateway: LLMGateway, prompts: PromptLibrary) -> ExtractStatements:
    """Step 11's extraction over the claim classifier (`claim_extract@v1`). The call is made as A7,
    in the step's own execution, so a re-run of the step is told apart in the call log."""

    async def run(ctx: AgentContext, copy: Mapping[str, str]) -> list[Statement]:
        return await extract_statements(gateway, prompts, ctx.call_context("A7"), copy, ctx.truth)

    return run


def gate_block_recorder(pool: AsyncConnectionPool[AsyncConnection]) -> RecordBlock:
    """Step 11's blocked reports into audit_log, with the round they came from. Every draft a gate
    blocks is kept, not only the last: the evaluation counts findings (for example the false-block
    rate of `citation_lacks_term`, pre-registered) over all of them."""

    async def run(state: RunState, report: GateReport) -> None:
        await record_gate_block(
            pool,
            org_id=UUID(state["org_id"]),
            product_id=UUID(state["product_id"]),
            run_id=UUID(state["run_id"]),
            report=report,
            extra={"revision_round": state.get("revision_round", 0)},
        )

    return run


def brief_checker(gateway: LLMGateway, prompts: PromptLibrary) -> CheckBrief:
    """Step 3's brief check over the claim classifier (`claim_extract@v2`). The brief is not output
    copy, so it is classified on the Standard tier whatever the sector (T4's strong tier is for the
    verification of regulated OUTPUT); the call is logged as agent ADMISSION at step 3."""

    async def run(ctx: AgentContext, texts: Mapping[str, str]) -> list[Statement]:
        return await extract_statements(
            gateway,
            prompts,
            ctx.call_context("ADMISSION"),
            texts,
            ctx.truth,
            tier=(Tier.STANDARD, "brief check at admission: flag and record, standard tier"),
        )

    return run


def memory_recaller(
    pool: AsyncConnectionPool[AsyncConnection],
    encoder: Encoder,
    cfg: ScoringConfig,
    *,
    today: Callable[[], date],
) -> Retrieve:
    """Steps 4 and 8 over `app.memory.recall`: candidates scored with E and, once the personas
    exist, with C. Read by the backend's own role and scoped by organisation and brand, like the
    context loader. The query is the category, the sector and the brief; no product or brand
    NAME."""

    async def run(state: RunState, personas: Sequence[str] | None) -> MemoryResult:
        product = state.get("product") or {}
        truth = state.get("truth") or {}
        sector = truth.get("sector")
        query = (
            " ".join(
                t
                for t in (
                    product.get("category"),
                    (sector or "").replace("_", " "),
                    state.get("brief"),
                )
                if t
            )
            or "marketing"
        )
        now = datetime.combine(today(), time.min, tzinfo=UTC)
        async with pool.connection() as conn:
            found = await recall(
                conn,
                encoder,
                cfg,
                org_id=UUID(state["org_id"]),
                query_text=query,
                product_id=product.get("id"),
                brand_id=product.get("brand_id"),
                product_category=product.get("category"),
                sector=sector,
                now=now,
                personas=personas,
            )
        return MemoryResult([i.record() for i in found.items], found.coverage)

    return run


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


def plan_request_from_state(state: RunState) -> PlanRequest:
    """The research plan's request, from the run: the product's category, its sector in words, and
    the AUDIENCE the request states. The evaluation's recorded queries carry the audience hint, so
    production must plan from the same fields or it asks other queries than the ones replayed.
    A sector id may use hyphens or underscores; both read as spaces, as in the test set."""
    product = state.get("product") or {}
    truth = state.get("truth") or {}
    sector = str(truth.get("sector") or "general").replace("_", " ").replace("-", " ")
    return PlanRequest(
        sector=sector,
        regulated=bool(truth.get("regulated")),
        category=product.get("category") or "product",
        audience=state.get("audience"),
    )


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
    declares its gap; only the evaluation's recording aborts a request).

    The plan comes from `plan_request_from_state`: category, sector and the request's stated
    audience. A3's personas cannot inform it: they come after this step (a design limit)."""

    async def run(state: RunState, depth: Depth) -> ResearchResult:
        request = plan_request_from_state(state)
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
            brand_domains=tuple((state.get("truth") or {}).get("brand_domains") or ()),
        )
        findings = [
            {
                "url": f.page.url,
                "title": f.page.title,
                "score": f.score.value,
                "components": dict(f.score.components),
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
        summaries = [p["summary"] for p in state.get("personas") or [] if p.get("summary")]
        if approved and summaries:
            # The run's personas, encoded as documents like the request side of C, travel with the
            # additions and are recorded for every item the campaign touches (migration 0037).
            stage.set_personas(
                [
                    PersonaVector(s, tuple(v), encoder.model, encoder.version)
                    for s, v in zip(summaries, encoder.embed_documents(summaries), strict=True)
                ]
            )
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
