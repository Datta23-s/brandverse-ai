"""Phase 6 chunk B: a deliberate, minimal, COUNTED live run of the real agents (owner, 2026-10-29).

One run of the graph at depth D0 (no search) on a non-regulated product in a throwaway TUNING
organisation (`is_practice`), with the real A3 (personas), A6 (copy), the claim classifier (step
11 extraction) and A7's risk read; A1 and A2 stay mocks. That is four Standard-tier calls on the
happy path; the owner approved six, and the run's call cap IS six, enforced by the ledger itself:

- STANDARD tier only. The strong tier is never enabled here, whatever the settings say.
- Logs to a THROWAWAY database (recreated by this script) and refuses a managed Supabase host: the
  live project's llm_calls and runs rows can never be deleted (0010, 0011). The live ledger cannot
  see these calls, so they are tallied by hand in Architecture.md section 4.
- No retry loop: the run stops at the first error. The gateway's own one repair is inside the cap.

Prints every llm_calls row the run produced, so the number of calls is reported from the log, not
from this script's count, and writes the run's outputs to evaluation/probes/.

Usage (from backend/):  PYTHONPATH=. uv run python scripts/chunk_b_live.py
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

from app.agents.copywriter import CopyWriter
from app.agents.mock import mock_agents
from app.agents.personas import PersonaAnalyst
from app.agents.risk import RiskReader
from app.core.config import get_settings
from app.core.gates import load_superlatives
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import Tier, registry_from
from app.memory.embeddings import HashingEncoder
from app.models.runs import RunEvent
from app.orchestrator.adapters import classifier_extractor, pool_context_loader, writeback_step
from app.orchestrator.checkpoint import open_checkpointer
from app.orchestrator.deps import GraphDeps, ResearchResult, no_memory
from app.orchestrator.graph import build_graph
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.orchestrator.state import RunState
from scripts.e2e_prepare_db import main as prepare_database
from tests.db.target_guard import managed_supabase_hosts

ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_chunk_b"
MAX_CALLS = 6
BRIEF = "A practical launch push for the monsoon season. Keep the tone plain and useful."
CLAIMS = ("Upper made of recycled mesh", "Weighs 240 g per shoe")
OUT = Path(__file__).resolve().parents[2] / "evaluation" / "probes"


class Sink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"


def seed(url: str) -> tuple[UUID, UUID, UUID, UUID]:
    """A tuning (practice) organisation with one non-regulated product and two verified claims."""
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
            "insert into organisations (name, is_practice) values ('Tuning (chunk B live)', true) "
            "returning id",
            (),
        )
        c.execute(
            "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')", (org, user)
        )
        brand = one(
            "insert into brands (org_id, name, sector) values (%s, 'Tuning brand', 'footwear') "
            "returning id",
            (org,),
        )
        product = one(
            "insert into products (org_id, brand_id, name, category) "
            "values (%s, %s, 'Tuning shoe', 'running shoes') returning id",
            (org, brand),
        )
        with c.transaction():
            c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(user), "role": "authenticated"}),),
            )
            for claim in CLAIMS:
                c.execute(
                    "insert into product_claims (org_id, product_id, claim, claim_type, "
                    "evidence_url, created_by) values (%s, %s, %s, 'verified', "
                    "'https://example.invalid/spec', 'human')",
                    (org, product, claim),
                )
        run = one(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (org, product),
        )
    return org, product, run, user


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    if not 1 <= args.max_calls <= MAX_CALLS:
        print(f"--max-calls must be 1 to {MAX_CALLS} (the owner approved {MAX_CALLS})")
        return 1
    url = prepare_database(ADMIN_URL, DB_NAME)  # refuses a managed host, recreates the database
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    org, product, run, user = seed(url)

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
            max_cost_per_run_usd=0.05,
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

        async def no_search(state: RunState, depth: object) -> ResearchResult:
            raise AssertionError("depth D0 must not search")

        agents = {
            **mock_agents(),
            "A3": PersonaAnalyst(gateway, prompts),
            "A6": CopyWriter(gateway, prompts),
            "A7": RiskReader(gateway, prompts),
        }
        deps = GraphDeps(
            events=sink,
            load_context=pool_context_loader(pool),
            retrieve=no_memory,
            research=no_search,
            agents=agents,
            write_memory=writeback_step(pool, HashingEncoder()),
            extract_statements=classifier_extractor(gateway, prompts),
            lexicon=load_superlatives("v1"),
            today=lambda: date.today(),
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
                    brief=BRIEF,
                    depth="D0",
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
        "database": DB_NAME,
        "cap": args.max_calls,
        "error": error,
        "status": outcome_state.get("status"),
        "calls": [
            {
                "agent": r[1],
                "step": r[2],
                "revision_round": r[3],
                "model": r[4],
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
        "personas": state.get("personas"),
        "creative": state.get("creative"),
        "validation": state.get("validation"),
        "errors": state.get("errors"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"chunk_b_live_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    print("report", path)
    return 0 if error is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
