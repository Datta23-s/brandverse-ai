"""Confirm the structured-output schema fix (app/llm/schema.py) against the real provider.

Approved by the project owner 2026-10-18: up to THREE cheap-tier calls. The failed V2 probe showed
the gateway could not send our structured schema to Gemini (400 INVALID_ARGUMENT); this sends the
classifier's schema — the one that failed — in its new provider-safe form, with the real
`claim_extract@v1` prompt, on the cheap tier, and stops at the first reply that validates.

- The ledger caps the run at 3 calls; a repair call counts.
- A non-transient error (a 400, invalid output after repair) stops at once: the budget is three,
  and a fourth is never spent. A transient network error may try again while calls remain.
- Refuses the live project; logs to a throwaway local database (brandverse_v2_probe).
- `--confirm "3 cheap-tier calls"` is required; without it nothing is sent.

Usage (from backend/):
  uv run python -m scripts.schema_confirm --database-url ...@127.0.0.1:55432/brandverse_v2_probe \\
      --confirm "3 cheap-tier calls"
"""

import argparse
import asyncio
import json
import sys
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.claim_classifier import CLAIM_EXTRACT, ExtractedStatements
from app.core.config import get_settings
from app.core.infrastructure import classify_infrastructure_failure
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import library_from
from app.llm.registry import Tier, registry_from
from tests.db.target_guard import managed_supabase_hosts

CONFIRMATION = "3 cheap-tier calls"
MAX_CALLS = 3
COPY = (
    "[body]\nTidewell Loop N2: built for the monsoon. IPX7 rating keeps the rain out. "
    "10 mm drivers bring every word through. Lasts all week."
)
CLAIMS = (
    "ipx7 | verified | IPX7 rating\n"
    "drivers | verified | 10 mm drivers\n"
    "60h | verified | Up to 60 hours playback (at 60% volume)"
)


def _one(row: tuple[object, ...] | None) -> object:
    if row is None:
        raise RuntimeError("insert returned no id")
    return row[0]


def prepare_run(url: str) -> CallContext:
    with psycopg.connect(url, autocommit=True) as c:
        org = _one(
            c.execute(
                "insert into organisations (name, is_test) values ('schema confirm', true) "
                "returning id"
            ).fetchone()
        )
        brand = _one(
            c.execute(
                "insert into brands (org_id, name) values (%s, 'confirm') returning id", (org,)
            ).fetchone()
        )
        product = _one(
            c.execute(
                "insert into products (org_id, brand_id, name) values (%s, %s, 'confirm') "
                "returning id",
                (org, brand),
            ).fetchone()
        )
        run = _one(
            c.execute(
                "insert into runs (org_id, product_id, request) values (%s, %s, %s) returning id",
                (org, product, json.dumps({"schema_confirm": True, "outside_evaluation": True})),
            ).fetchone()
        )
    return CallContext(org_id=UUID(str(org)), run_id=UUID(str(run)), agent_id="schema-confirm")


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if managed_supabase_hosts(args.database_url):
        print("refusing: never logs to the live project")
        return 1
    if args.confirm != CONFIRMATION:
        print(f'nothing sent. Pass --confirm "{CONFIRMATION}" to make the approved calls.')
        return 0
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    registry = registry_from(settings.models_config_path)
    prompts = library_from(settings.prompts_dir)
    ctx = prepare_run(args.database_url)
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        args.database_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    outcome = "not confirmed"
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=MAX_CALLS,
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
            allow_strong_tier=False,  # cheap tier only
        )
        prompt = prompts.get(*CLAIM_EXTRACT).render(copy=COPY, claims=CLAIMS)
        while True:
            try:
                result = await gateway.structured(
                    ExtractedStatements,
                    tier=Tier.CHEAP,
                    prompt=prompt,
                    ctx=ctx,
                    route_reason="schema fix confirmation (approved: 3 cheap-tier calls)",
                    max_output_tokens=1024,
                )
                print("VALID REPLY:", result.model_dump_json(indent=2))
                outcome = "confirmed"
                break
            except Exception as e:  # report; stop unless transient and calls remain
                print(f"FAILED: {type(e).__name__}: {e}")
                if classify_infrastructure_failure(e) != "network_error":
                    print("stopping: non-transient error; no further call is spent")
                    break
    finally:
        await pool.close()

    with psycopg.connect(args.database_url) as c:
        rows = c.execute(
            "select id, model, tier, outcome, input_tokens, output_tokens, latency_ms, "
            "prompt_id, prompt_version from llm_calls where run_id = %s order by id",
            (ctx.run_id,),
        ).fetchall()
    made = [r for r in rows if r[3] != "refused"]
    print(f"outcome: {outcome}; llm_calls rows {len(rows)}, real calls made {len(made)} (cap 3)")
    for r in rows:
        print("  ", r)
    return 0 if outcome == "confirmed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--confirm", default="")
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
