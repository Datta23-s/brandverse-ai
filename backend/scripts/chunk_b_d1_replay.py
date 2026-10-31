"""Phase 6 chunk B: ONE full D1 run of the whole graph, real models, REPLAYED evidence (owner,
2026-10-30).

The first time the graph runs on real models against real evidence: request R10 of the evaluation
set (B04 product A, footwear, non-regulated) through A1, A2, A3, A6, the claim classifier and A7,
with the research step reading the evaluation SNAPSHOT (`SnapshotSearch`, `SnapshotPages`): zero
live searches, zero live fetches, so it does not touch the recording. Only the model calls are
live.

- Approved: 10 Standard-tier calls in the tuning organisation, enough for one pass of the graph
  and a revision round. The run's call cap IS ten, enforced by the ledger. STANDARD tier only; the
  strong tier is never enabled here. No retry loop: the run stops at the first error, or at the
  cap, and reports.
- Before any model call, the research step is replayed alone (free) to prove the snapshot answers
  every planned query; a miss aborts with nothing spent.
- Logs to a THROWAWAY database (recreated here) and refuses a managed Supabase host; the live
  ledger cannot see these calls, so they are tallied by hand in Architecture.md section 4.
- The run parks at the approval step; nothing here approves it, so nothing is written to memory.
- The full report (it holds third-party text: A2's quotations) is written OUTSIDE the repository,
  under C:\\BrandverseData\\evaluation-runs; only counts and the product's own copy are printed.

Usage (from backend/):  PYTHONPATH=. uv run python scripts/chunk_b_d1_replay.py
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import Agent
from app.agents.copywriter import CopyWriter
from app.agents.market import MarketAnalyst
from app.agents.personas import PersonaAnalyst
from app.agents.product_brand import ProductBrandAnalyst
from app.agents.risk import RiskReader
from app.core.config import get_settings
from app.core.gates import load_superlatives
from app.evaluation.test_set import request_inputs as evaluation_inputs
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import Tier, registry_from
from app.memory.embeddings import HashingEncoder
from app.memory.scoring import load_scoring
from app.models.runs import RunEvent
from app.orchestrator.adapters import (
    PageEvidence,
    classifier_extractor,
    driver_research,
    memory_recaller,
    pool_context_loader,
    writeback_step,
)
from app.orchestrator.checkpoint import open_checkpointer
from app.orchestrator.deps import GraphDeps
from app.orchestrator.graph import build_graph
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.orchestrator.state import RunState
from app.research.extraction import Extractor, trafilatura_extract
from app.research.snapshot import SnapshotPages, SnapshotSearch, SnapshotStore
from scripts.e2e_prepare_db import main as prepare_database
from tests.db.target_guard import managed_supabase_hosts

ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_chunk_b"
STORE = Path("C:/BrandverseData/evaluation-snapshot")
OUT = Path("C:/BrandverseData/evaluation-runs")
MAX_CALLS = 10
REQUEST_ID = "R10"


class Sink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"


def request_inputs() -> dict[str, object]:
    """R10's brand, product, claims and brief, read from the evaluation set (pseudonyms only)."""
    r = evaluation_inputs()[REQUEST_ID]
    return {
        "brand": r.brand,
        "sector": r.sector,
        "product": r.product,
        "category": r.category,
        "claims": list(r.claims),
        "evidence_url": r.evidence_url,
        "brief": r.brief,
        "audience": r.audience,
        "domains": list(r.domains),
    }


def seed(url: str, inp: dict[str, object]) -> tuple[UUID, UUID, UUID, UUID]:
    """A tuning (practice) organisation holding R10's product and its verified claims."""
    user = uuid4()
    with psycopg.connect(url, autocommit=True) as c:

        def one(query: str, params: tuple[object, ...]) -> UUID:
            row = c.execute(query, params).fetchone()
            if row is None:
                raise RuntimeError("insert returned no row")
            value: UUID = row[0]
            return value

        c.execute("insert into auth.users (id) values (%s)", (user,))
        org = one(
            "insert into organisations (name, is_practice) values ('Tuning (D1 replay)', true) "
            "returning id",
            (),
        )
        c.execute(
            "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')", (org, user)
        )
        brand = one(
            "insert into brands (org_id, name, sector, domains) "
            "values (%s, %s, %s, %s) returning id",
            (org, inp["brand"], inp["sector"], inp.get("domains", [])),
        )
        product = one(
            "insert into products (org_id, brand_id, name, category) "
            "values (%s, %s, %s, %s) returning id",
            (org, brand, inp["product"], inp["category"]),
        )
        with c.transaction():
            c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(user), "role": "authenticated"}),),
            )
            for claim in list(inp["claims"]):  # type: ignore[call-overload]
                c.execute(
                    "insert into product_claims (org_id, product_id, claim, claim_type, "
                    "evidence_url, created_by) values (%s, %s, %s, 'verified', %s, 'human')",
                    (org, product, claim, inp["evidence_url"]),
                )
        run = one(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (org, product),
        )
    return org, product, run, user


def write_report(report: dict[str, object]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"d1_replay_{REQUEST_ID}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    return path


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    if not 1 <= args.max_calls <= MAX_CALLS:
        print(f"--max-calls must be 1 to {MAX_CALLS} (the owner approved {MAX_CALLS})")
        return 1
    cfg = load_scoring()
    research_cfg = cfg.research
    store = SnapshotStore(STORE)
    if REQUEST_ID not in store.committed:
        print(f"{REQUEST_ID} is not committed in the snapshot")
        return 1
    extractor = Extractor(
        trafilatura_extract,
        min_chars=research_cfg.extraction_min_chars,
        max_chars=research_cfg.extraction_max_chars,
    )
    pages = SnapshotPages(store)
    research = driver_research(
        SnapshotSearch(store, research_cfg.declared_engines),
        pages,
        extractor,
        cfg.source,
        queries_per_facet=research_cfg.planner_queries_per_facet,
        locale=research_cfg.planner_locale,
        max_pages_per_query=research_cfg.max_pages_per_query,
        today=lambda: date.today(),
    )
    inp = request_inputs()

    # Free pre-flight: the research step alone, against the snapshot. A miss spends nothing.
    probe = RunState(
        product={"category": inp["category"]},
        truth={
            "sector": inp["sector"],
            "regulated": False,
            "brand_domains": inp.get("domains", []),
        },
        audience=str(inp["audience"]),
    )
    try:
        pre = await research(probe, "D1")
    except Exception as e:
        print(f"PRE-FLIGHT FAILED, nothing spent: {type(e).__name__}: {e}")
        return 1
    print(
        f"pre-flight (free): {len(pre.findings)} findings from the snapshot; "
        f"report {json.dumps(pre.report)}"
    )

    if args.preflight_only:
        return 0
    url = prepare_database(ADMIN_URL, DB_NAME)  # refuses a managed host, recreates the database
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    org, product, run, user = seed(url, inp)
    registry = registry_from(settings.models_config_path)
    print(f"database {urlsplit(url).hostname}/{DB_NAME}; tuning organisation {org}")
    standard = registry.model(Tier.STANDARD)
    print(f"cap {args.max_calls} call(s); standard tier -> {standard.litellm_model}")

    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url, min_size=0, max_size=3, open=False
    )
    await pool.open()
    sink = Sink()
    outcome_state: dict[str, object] = {}
    error: str | None = None
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=args.max_calls,
            max_cost_per_run_usd=0.25,
            pending_expiry_s=settings.llm_pending_expiry_s,
        )
        prompts = library_from(settings.prompts_dir)
        gateway = LLMGateway(
            registry,
            ledger,
            prompts,
            api_key=settings.google_api_key.get_secret_value(),
            timeout_s=60,
            max_rate_limit_waits=1,
            max_rate_limit_wait_s=30,
            allow_strong_tier=False,  # never, whatever the settings say
        )
        encoder = HashingEncoder()
        agents: dict[str, Agent] = {
            "A1": ProductBrandAnalyst(gateway, prompts),
            "A2": MarketAnalyst(gateway, prompts),
            "A3": PersonaAnalyst(gateway, prompts),
            "A6": CopyWriter(gateway, prompts),
            "A7": RiskReader(gateway, prompts),
        }
        deps = GraphDeps(
            events=sink,
            load_context=pool_context_loader(pool),
            retrieve=memory_recaller(pool, encoder, cfg, today=lambda: date.today()),
            research=research,
            agents=agents,
            write_memory=writeback_step(pool, encoder),
            extract_statements=classifier_extractor(gateway, prompts),
            lexicon=load_superlatives("v1"),
            today=lambda: date.today(),
            evidence=PageEvidence(pages, extractor),
        )
        async with open_checkpointer(url) as saver:
            pipeline = Pipeline(
                build_graph(deps, saver),
                sink,
                PoolRunLedger(pool),
                max_revisions=deps.max_revisions,
            )
            try:
                out = await pipeline.start(
                    run_id=run,
                    org_id=org,
                    user_id=user,
                    product_id=product,
                    brief=str(inp["brief"]),
                    depth="D1",
                    audience=str(inp["audience"]),
                )
                outcome_state = {"status": out.status, "state": out.state}
                print(f"run status: {out.status}")
            except Exception as e:  # report, do not retry
                error = f"{type(e).__name__}: {e}"
                print(f"FAILED: {error}")
    finally:
        await pool.close()

    with psycopg.connect(url) as c:
        rows = c.execute(
            "select id, agent_id, step, revision_round, model, tier, prompt_id, prompt_version, "
            "outcome, input_tokens, output_tokens, cost_usd, latency_ms, rate_limit_wait_ms "
            "from llm_calls where run_id = %s order by id",
            (run,),
        ).fetchall()
        run_row = c.execute(
            "select status, llm_call_count, total_cost_usd, declared_gaps from runs where id = %s",
            (run,),
        ).fetchone()
    real = [r for r in rows if r[8] != "refused"]
    print(f"llm_calls rows for this run: {len(rows)}; real calls made: {len(real)}")
    for r in rows:
        print("  ", r)
    print("run row:", run_row)

    raw_state = outcome_state.get("state")
    state: dict[str, object] = raw_state if isinstance(raw_state, dict) else {}
    report = {
        "date_utc": datetime.now(UTC).isoformat(),
        "request": REQUEST_ID,
        "database": DB_NAME,
        "cap": args.max_calls,
        "error": error,
        "status": outcome_state.get("status"),
        "run_row": [str(x) for x in run_row] if run_row else None,
        "calls": [
            {
                "agent": r[1],
                "step": r[2],
                "revision_round": r[3],
                "tier": r[5],
                "prompt": f"{r[6]}@{r[7]}",
                "outcome": r[8],
                "input_tokens": r[9],
                "output_tokens": r[10],
                "cost_usd": float(r[11]) if r[11] is not None else None,
                "latency_ms": r[12],
                "rate_limit_wait_ms": r[13],
            }
            for r in rows
        ],
        "state": {
            k: state.get(k)
            for k in (
                "research",
                "findings",
                "memory_hits",
                "agent_outputs",
                "personas",
                "strategy",
                "creative",
                "validation",
                "revision_round",
                "errors",
            )
        },
        "events": [type(e).__name__ for e in sink.events],
    }
    path = write_report(report)
    print("report (outside the repository)", path)
    return 0 if error is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    parser.add_argument("--preflight-only", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
