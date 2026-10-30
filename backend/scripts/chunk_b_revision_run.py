"""Phase 6 chunk B: a FORCED REVISION ROUND on real models (owner, 2026-10-30).

The revision loop (bounded at 2 rounds) and the exit criterion beside it have never run on a real
model. The first full D1 run passed its gates on the first draft, so nothing was revised. This
reuses that run, FREE where it can:

- A1, A2 and A3 return exactly what they returned in the saved report of the 2026-10-30 run (no
  call), and research replays the snapshot (no search).
- A6's FIRST draft is the copy that run wrote, replayed (no call): the headline says "lightweight"
  and no claim backs it (threat V11). The first draft is therefore the one that SHOULD fail.
- A7's advisory is a mock (no call).
- What is live: the claim classifier (`claim_extract@v2`), which must now extract "lightweight"
  and so block the draft; then A6's REVISION; then the classifier again on the second draft. Three
  calls, the three that remained of the ten approved; the run's call cap IS three.

Standard tier only, a throwaway database, no retry loop. If the first draft passes (v2 missed the
attribute), nothing is revised and the run says so. If the second draft fails too, the run stops
at the cap (the third round's call is refused) and reports. The run parks at approval; nothing
approves.

Usage (from backend/):  PYTHONPATH=. uv run python scripts/chunk_b_revision_run.py
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

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import Agent, AgentContext
from app.agents.copywriter import CopyWriter
from app.agents.mock import MockA7
from app.agents.outputs import CreativeOutput, CustomerOutput, MarketOutput, ProductBrandOutput
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

MAX_CALLS = 3


class Replayed:
    """An agent that returns what it returned in the saved run."""

    def __init__(self, agent_id: str, output: Any) -> None:
        self.id, self._output = agent_id, output

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> Any:
        return self._output


class FirstDraftThenReal:
    """A6: the saved first draft, then the real writer for every revision."""

    id = "A6"

    def __init__(self, first: CreativeOutput, real: CopyWriter) -> None:
        self._first, self._real = first, real
        self.drafts: list[dict[str, str]] = []

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
        out = self._first if state.get("creative") is None else await self._real.run(state, ctx)
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


async def main() -> int:
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
    print(f"database {urlsplit(url).hostname}/{DB_NAME}; cap {MAX_CALLS} call(s); standard tier")

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
            max_calls_per_run=MAX_CALLS,
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
        writer = FirstDraftThenReal(first_copy, CopyWriter(gateway, prompts))
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
            "A1": Replayed("A1", ProductBrandOutput.model_validate(outputs["A1"])),
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
            "validation": validation,
            "revision_round": outcome_state.get("revision_round"),
            "errors": outcome_state.get("errors"),
            "step_events": [[str(x) for x in s] for s in steps],
        }
    )
    print("report (outside the repository)", path)
    return 0 if error is None else 2


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    sys.exit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
