"""Phase 6 chunk B: the FORCED REVISION on real models, two modes (owner, 2026-10-30).

The first run of this (3 calls) showed `claim_extract@v2` catching "lightweight walking shoe" and
the gate blocking it, and the revision NOT removing it: A6 was told the kind of problem and not
where, and "lightweight" was also in A1's positioning and in the brief. Since then: the reviser is
told the offending span for `unsourced_claim` (X), `a1_positioning@v2` keeps product properties
out of the positioning, and the brief is checked once per request (Y). Standard tier only, a
throwaway database, no retry loop, the run's call cap IS the number given; it parks at approval.

- X (the reviser alone, cap 5): A1, A2, A3 and A6's first draft are replayed from the saved first
  run, so A1's positioning still says "lightweight" and the first draft is the one that failed.
  Live: the classifier on draft 1, A6's revision (told the span), the classifier again, and a
  second round if needed. Isolates whether naming the span lets the reviser repair it.
- Y (the full fix, cap 7): A2 and A3 are replayed; A1 (v2), A6's first draft, the classifier and
  the revision are live, and the brief check is made FIRST, once, as the evaluation's precompute
  does, and handed to the run as the request's record (admission makes no call of its own).
  Reports what the brief check flags on R10: ONE request, so it gives no rate across the 30.

A7's advisory is a mock in both. If a first draft passes, nothing is revised and the run says so.

Usage (from backend/):  PYTHONPATH=. uv run python scripts/chunk_b_revision_run.py --mode x|y
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import Agent, AgentContext
from app.agents.copywriter import CopyWriter
from app.agents.mock import MockA7
from app.agents.outputs import CreativeOutput, CustomerOutput, MarketOutput, ProductBrandOutput
from app.agents.product_brand import ProductBrandAnalyst
from app.core.brief_check import assess
from app.core.config import get_settings
from app.core.gates import Statement, load_superlatives
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import registry_from
from app.memory.embeddings import HashingEncoder
from app.memory.scoring import load_scoring
from app.orchestrator.adapters import (
    PageEvidence,
    brief_checker,
    classifier_extractor,
    driver_research,
    memory_recaller,
    pool_context_loader,
    writeback_step,
)
from app.orchestrator.checkpoint import open_checkpointer
from app.orchestrator.deps import ExtractStatements, GraphDeps
from app.orchestrator.graph import build_graph
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.research.extraction import Extractor, trafilatura_extract
from app.research.snapshot import SnapshotPages, SnapshotSearch, SnapshotStore
from scripts.chunk_b_d1_replay import (
    ADMIN_URL,
    DB_NAME,
    OUT,
    REQUEST_ID,
    STORE,
    Sink,
    request_inputs,
    seed,
)
from scripts.e2e_prepare_db import main as prepare_database
from tests.db.target_guard import managed_supabase_hosts

CAPS = {"x": 5, "y": 7}  # 12 approved in all


class Replayed:
    """An agent that returns what it returned in the saved run."""

    def __init__(self, agent_id: str, output: Any) -> None:
        self.id, self._output = agent_id, output

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> Any:
        return self._output


class FirstDraftThenReal:
    """A6: the saved first draft, then the real writer for every revision."""

    id = "A6"

    def __init__(self, first: CreativeOutput, real: CopyWriter, *, replay_first: bool) -> None:
        self._first, self._real, self._replay = first, real, replay_first
        self.drafts: list[dict[str, str]] = []

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        fresh = state.get("creative") is None
        out = self._first if fresh and self._replay else await self._real.run(state, ctx)
        self.drafts.append(dict(out.copy_))
        return out


def latest_report() -> dict[str, Any]:
    path = sorted(OUT.glob(f"d1_replay_{REQUEST_ID}_*.json"))[-1]
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report


def write_report(report: dict[str, object]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"revision_{REQUEST_ID}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    return path


async def main(mode: str, cap_override: int | None) -> int:
    cap = cap_override or CAPS[mode]
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    saved = latest_report()["state"]
    outputs = saved["agent_outputs"]
    first_copy = CreativeOutput(copy=saved["creative"]["copy"])
    inp = request_inputs()
    cfg = load_scoring()
    research_cfg = cfg.research
    store = SnapshotStore(STORE)
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
    url = prepare_database(ADMIN_URL, DB_NAME)
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    org, product, run, user = seed(url, inp)
    registry = registry_from(settings.models_config_path)
    print(f"database {urlsplit(url).hostname}/{DB_NAME}; mode {mode}, cap {cap}; standard tier")

    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url, min_size=0, max_size=3, open=False
    )
    await pool.open()
    sink = Sink()
    outcome_state: dict[str, Any] = {}
    status: str | None = None
    error: str | None = None
    extractions: list[dict[str, Any]] = []
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=cap,
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
            allow_strong_tier=False,
        )
        encoder = HashingEncoder()
        real_writer = CopyWriter(gateway, prompts)
        writer = FirstDraftThenReal(first_copy, real_writer, replay_first=mode == "x")
        real_extract = classifier_extractor(gateway, prompts)

        async def recording_extract(ctx: AgentContext, copy: Mapping[str, str]) -> list[Statement]:
            statements = await real_extract(ctx, copy)
            extractions.append(
                {
                    "round": ctx.revision_round,
                    "statements": [(s.text, s.claim_id) for s in statements],
                }
            )
            return statements

        extract: ExtractStatements = recording_extract
        agents: dict[str, Agent] = {
            "A1": (
                Replayed("A1", ProductBrandOutput.model_validate(outputs["A1"]))
                if mode == "x"
                else ProductBrandAnalyst(gateway, prompts)
            ),
            "A2": Replayed("A2", MarketOutput.model_validate(outputs["A2"])),
            "A3": Replayed("A3", CustomerOutput.model_validate({"personas": saved["personas"]})),
            "A6": writer,
            "A7": MockA7(),
        }
        deps = GraphDeps(
            events=sink,
            load_context=pool_context_loader(pool),
            retrieve=memory_recaller(pool, encoder, cfg, today=lambda: date.today()),
            research=research,
            agents=agents,
            write_memory=writeback_step(pool, encoder),
            extract_statements=extract,
            lexicon=load_superlatives("v1"),
            today=lambda: date.today(),
            evidence=PageEvidence(pages, extractor),
        )
        precomputed: dict[str, Any] | None = None
        if mode == "y":
            loaded = await pool_context_loader(pool)(org, product)
            if loaded is None:
                raise RuntimeError("the product did not load")
            bctx = AgentContext(run, org, loaded.truth, date.today(), step=3, execution_id=uuid4())
            statements = await brief_checker(gateway, prompts)(bctx, {"brief": str(inp["brief"])})
            precomputed = assess(str(inp["brief"]), loaded.truth, statements)
            print("brief check (precomputed, one request):", json.dumps(precomputed))
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
                    brief_check=precomputed,
                )
                status, outcome_state = out.status, dict(out.state)
                print(f"run status: {status}")
            except Exception as e:  # report, do not retry
                error = f"{type(e).__name__}: {e}"
                print(f"FAILED: {error}")
    finally:
        await pool.close()

    with psycopg.connect(url) as c:
        rows = c.execute(
            "select id, agent_id, step, revision_round, tier, prompt_id, prompt_version, outcome, "
            "input_tokens, output_tokens, cost_usd, latency_ms from llm_calls where run_id = %s "
            "order by id",
            (run,),
        ).fetchall()
    real = [r for r in rows if r[7] != "refused"]
    print(f"llm_calls rows: {len(rows)}; real calls made: {len(real)}")
    for r in rows:
        print("  ", r)
    for ex in extractions:
        print("extraction, round", ex["round"])
        for text, claim in ex["statements"]:
            print("   ", repr(text[:90]), "->", claim)
    print("drafts:", len(writer.drafts))
    for i, d in enumerate(writer.drafts):
        print(f"--- draft {i}")
        for k, v in d.items():
            print(f"  [{k}] {v}")
    validation = outcome_state.get("validation") or {}
    print("brief_check in the run:", json.dumps(outcome_state.get("brief_check")))
    print(
        "positioning:",
        ((outcome_state.get("agent_outputs") or {}).get("A1") or {}).get("positioning"),
    )
    print("final outcome", validation.get("outcome"), "findings", validation.get("findings"))
    print(
        "revision_round", outcome_state.get("revision_round"), "errors", outcome_state.get("errors")
    )
    steps = [
        (getattr(e, "step", None), str(getattr(e, "status", "")), getattr(e, "detail", None))
        for e in sink.events
        if getattr(e, "step", None) in (10, 11, 12)
    ]
    for s in steps:
        print("   step event", s)
    path = write_report(
        {
            "date_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "error": error,
            "calls": [[str(x) for x in r] for r in rows],
            "extractions": extractions,
            "drafts": writer.drafts,
            "mode": mode,
            "brief_check": outcome_state.get("brief_check"),
            "positioning": ((outcome_state.get("agent_outputs") or {}).get("A1") or {}).get(
                "positioning"
            ),
            "validation": validation,
            "revision_round": outcome_state.get("revision_round"),
            "errors": outcome_state.get("errors"),
            "step_events": [[str(x) for x in s] for s in steps],
        }
    )
    print("report (outside the repository)", path)
    return 0 if error is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["x", "y"], required=True)
    parser.add_argument("--cap", type=int, help="fewer calls than the mode's default")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.mode, args.cap), loop_factory=asyncio.SelectorEventLoop))
