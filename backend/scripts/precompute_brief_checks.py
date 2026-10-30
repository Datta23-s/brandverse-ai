"""Precompute the evaluation's brief checks: ONE model call per request, frozen with its inputs.

Not a cache: the brief check is a property of the REQUEST (app/core/brief_check.py), computed once
here and read by every arm, so the arms cannot be flagged differently by a model's variation. The
result is written to evaluation/brief_checks.json with its lock (app/evaluation/brief_checks.py).

- One Standard-tier call per request (`claim_extract@v2`, agent ADMISSION, step 3), the run's call
  cap is the number of requests asked for, and there is no retry loop. `--max-calls` has no default:
  the calls are approved by the owner, by number, before this runs.
- Logs to a THROWAWAY database (recreated here); refuses a managed Supabase host.
- Each request is checked against its own VERIFIED claims, the ones the evaluation enters.
- A request already frozen is not remade unless `--remake` names it, and the lock then changes.

Usage (from backend/):
    PYTHONPATH=. uv run python scripts/precompute_brief_checks.py --requests R10 --max-calls 1
    PYTHONPATH=. uv run python scripts/precompute_brief_checks.py --requests all --max-calls 30
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import AgentContext
from app.core.brief_check import assess
from app.core.config import get_settings
from app.evaluation import brief_checks
from app.evaluation.test_set import request_inputs
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import Tier, registry_from
from app.orchestrator.adapters import brief_checker, pool_context_loader
from scripts.chunk_b_d1_replay import ADMIN_URL, DB_NAME, seed
from scripts.e2e_prepare_db import main as prepare_database
from tests.db.target_guard import managed_supabase_hosts


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    inputs = request_inputs()
    wanted = list(inputs) if args.requests == "all" else args.requests.split(",")
    unknown = [r for r in wanted if r not in inputs]
    if unknown:
        print(f"unknown requests: {unknown}")
        return 1
    try:
        frozen = brief_checks.load()
    except brief_checks.FrozenBriefChecksError:
        frozen = {}
    todo = [r for r in wanted if r not in frozen or r in args.remake.split(",")]
    skipped = [r for r in wanted if r not in todo]
    if skipped:
        print(f"already frozen, not remade: {skipped}")
    if len(todo) > args.max_calls:
        print(f"{len(todo)} calls needed, --max-calls is {args.max_calls}: nothing made")
        return 1
    if not todo:
        return 0
    url = prepare_database(ADMIN_URL, DB_NAME)
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    registry = registry_from(settings.models_config_path)
    standard = registry.model(Tier.STANDARD)
    print(
        f"{len(todo)} request(s); cap {args.max_calls}; standard tier -> {standard.litellm_model}"
    )
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url, min_size=0, max_size=3, open=False
    )
    await pool.open()
    made: dict[str, dict[str, object]] = {}
    try:
        prompts = library_from(settings.prompts_dir)
        for rid in todo:
            request = inputs[rid]
            org, product, run, _ = seed(
                url,
                {
                    "brand": request.brand,
                    "sector": request.sector,
                    "product": request.product,
                    "category": request.category,
                    "claims": list(request.claims),
                    "evidence_url": request.evidence_url,
                },
            )
            ledger = CallLedger(
                pool,
                registry,
                max_calls_per_run=2,  # one call, one repair at most, per request
                max_cost_per_run_usd=0.05,
                pending_expiry_s=settings.llm_pending_expiry_s,
            )
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
            loaded = await pool_context_loader(pool)(org, product)
            if loaded is None:
                print(f"{rid}: product did not load")
                return 2
            ctx = AgentContext(run, org, loaded.truth, date.today(), step=3, execution_id=uuid4())
            try:
                statements = await brief_checker(gateway, prompts)(ctx, {"brief": request.brief})
            except Exception as e:  # report, do not retry
                print(f"{rid}: FAILED {type(e).__name__}: {e}")
                return 2
            record = assess(request.brief, loaded.truth, statements)
            record |= {
                "request": rid,
                "model": standard.litellm_model,
                "checked_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            made[rid] = record
            print(f"{rid}: {len(record['unbacked'])} unbacked, {record['backed']} backed")
    finally:
        await pool.close()
    if args.dry:
        print(json.dumps(made, indent=1))
        return 0
    brief_checks.freeze({**frozen, **made})
    print(f"frozen {len(made)} record(s) in evaluation/brief_checks.json; the lock was rewritten")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", required=True, help="R10, R10,R11 or all")
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--remake", default="", help="requests already frozen to make again")
    parser.add_argument("--dry", action="store_true", help="print the records, freeze nothing")
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
