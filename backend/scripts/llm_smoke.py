"""Live verification of the call path: a deliberate, minimal, COUNTED real model call.

Quota discipline (Phase 2):
- CHEAP tier only. The strong tier's model allows 20 requests a day; this script refuses it.
- A hard cap on calls, enforced by the ledger itself: the run's call cap is set to `--max-calls`
  (default 1, at most 2 — one call, plus one repair if its output does not validate). No loop.
- Rate-limit waits capped at 1 wait / 30 s.
- Logs to a THROWAWAY database (created with scripts/e2e_prepare_db.py) unless `--allow-live-db`
  is given: the live project's llm_calls and runs rows can never be deleted (0010, 0011).

Prints every llm_calls row the run produced, so the actual number of calls is reported from the
log, not from this script's own count.

Usage (from backend/):
  uv run python -m scripts.llm_smoke --database-url postgresql://...@127.0.0.1:55432/brandverse_e2e
"""

import argparse
import asyncio
import sys
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.llm.gateway import LLMGateway, Prompt
from app.llm.ledger import CallContext, CallLedger
from app.llm.registry import Tier, registry_from
from tests.db.target_guard import managed_supabase_hosts


class CapitalCity(BaseModel):
    """A tiny schema with one constrained field, to exercise structured output."""

    country: str = Field(min_length=1, max_length=60)
    capital: str = Field(min_length=1, max_length=60)
    confidence: float = Field(ge=0, le=1)


def _id(row: tuple[object, ...] | None) -> object:
    if row is None:
        raise RuntimeError("insert returned no id")
    return row[0]


def prepare_run(url: str) -> CallContext:
    with psycopg.connect(url, autocommit=True) as c:
        org = _id(
            c.execute(
                "insert into organisations (name) values ('LLM smoke test') returning id"
            ).fetchone()
        )
        brand = _id(
            c.execute(
                "insert into brands (org_id, name) values (%s, 'smoke') returning id", (org,)
            ).fetchone()
        )
        product = _id(
            c.execute(
                "insert into products (org_id, brand_id, name) values (%s, %s, 'smoke') "
                "returning id",
                (org, brand),
            ).fetchone()
        )
        run = _id(
            c.execute(
                "insert into runs (org_id, product_id, request) "
                "values (%s, %s, '{\"smoke\": true}') returning id",
                (org, product),
            ).fetchone()
        )
    return CallContext(org_id=UUID(str(org)), run_id=UUID(str(run)), agent_id="llm-smoke")


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    if managed_supabase_hosts(args.database_url) and not args.allow_live_db:
        print("refusing to log to the live project without --allow-live-db (rows are permanent)")
        return 1
    if not 1 <= args.max_calls <= 2:
        print("--max-calls must be 1 or 2")
        return 1

    registry = registry_from(settings.models_config_path)
    tier = Tier.CHEAP  # never strong: 20 requests/day
    ctx = prepare_run(args.database_url)
    print(
        f"tier {tier.value} -> {registry.model(tier).litellm_model}; cap {args.max_calls} call(s)"
    )
    print(f"database {urlsplit(args.database_url).hostname}/{urlsplit(args.database_url).path[1:]}")

    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        args.database_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=args.max_calls,
            max_cost_per_run_usd=0.05,
            pending_expiry_s=settings.llm_pending_expiry_s,
        )
        gateway = LLMGateway(
            registry,
            ledger,
            api_key=settings.google_api_key.get_secret_value(),
            timeout_s=60,
            max_rate_limit_waits=1,
            max_rate_limit_wait_s=30,
            allow_strong_tier=False,  # never, whatever the settings say
        )
        try:
            result = await gateway.structured(
                CapitalCity,
                tier=tier,
                prompt=Prompt(
                    system="You answer geography questions as JSON matching the given schema.",
                    user="What is the capital of Portugal?",
                ),
                ctx=ctx,
                route_reason="smoke test: Phase 2 chunk A live verification (cheap tier)",
                max_output_tokens=128,
            )
            print(f"result: {result!r}")
        except Exception as e:  # report, do not retry
            print(f"FAILED: {type(e).__name__}: {e}")
    finally:
        await pool.close()

    with psycopg.connect(args.database_url) as c:
        rows = c.execute(
            "select id, model, tier, outcome, input_tokens, output_tokens, cost_usd, latency_ms, "
            "rate_limit_wait_ms, finished_at - created_at, route_reason "
            "from llm_calls where run_id = %s order by id",
            (ctx.run_id,),
        ).fetchall()
    counted = [r for r in rows if r[3] != "refused"]
    print(f"llm_calls rows for this run: {len(rows)}; real calls made: {len(counted)}")
    for r in rows:
        print("  ", r)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--max-calls", type=int, default=1)
    parser.add_argument("--allow-live-db", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
