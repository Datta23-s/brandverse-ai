"""One live call of A7's risk read on a PLANTED copy (owner-approved six calls, 2026-10-29).

The first live run (scripts/chunk_b_live.py) gave A7 clean copy, so it flagged nothing and showed
nothing about whether a real model flags AND quotes the copy exactly: the property the advisory's
dropped-quote guard depends on. This sends A7 a copy written to show four of the eight anti-patterns
(it is PLANTED, not model output, and the report says so) and prints what comes back.

Standard tier, call cap 1 enforced by the ledger, the existing throwaway database (not recreated, so
the first run's rows stay), no retry. Usage: PYTHONPATH=. uv run python scripts/chunk_b_a7_probe.py
"""

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agents.base import AgentContext
from app.agents.risk import RiskReader
from app.core.config import get_settings
from app.core.truth import ProductTruth
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import registry_from
from scripts.chunk_b_live import ADMIN_URL, DB_NAME, OUT, seed
from tests.db.target_guard import managed_supabase_hosts

PLANTED = {
    "headline": "Only 3 pairs left today - don't miss out!",
    "body": (
        "Join 10,000 happy runners. Everyone is buying these. Was Rs 9,999, now Rs 2,999. "
        "Click 'No thanks, I'd rather keep running slow' to leave."
    ),
}
EXPECTED = {"false_scarcity", "fabricated_social_proof", "price_anchoring", "confirmshaming"}


async def main() -> int:
    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    org, product, run, _ = seed(url)
    registry = registry_from(settings.models_config_path)
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    result: dict[str, object] = {}
    error: str | None = None
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=1,
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
            allow_strong_tier=False,
        )
        ctx = AgentContext(
            run, org, ProductTruth(None, (), ()), date.today(), step=11, execution_id=uuid4()
        )
        try:
            advisory = await RiskReader(gateway, prompts).run({"creative": {"copy": PLANTED}}, ctx)
            result = advisory.model_dump(mode="json")
        except Exception as e:  # report, do not retry
            error = f"{type(e).__name__}: {e}"
    finally:
        await pool.close()
    with psycopg.connect(url) as c:
        rows = c.execute(
            "select agent_id, step, tier, prompt_id, outcome, input_tokens, output_tokens "
            "from llm_calls where run_id = %s",
            (run,),
        ).fetchall()
    flags = result.get("flags") or []
    found = {f["category"] for f in flags} if isinstance(flags, list) else set()
    print("calls:", rows, "error:", error)
    print("flags:", json.dumps(flags, indent=1))
    print("dropped_unverifiable:", result.get("dropped_unverifiable"))
    print(
        "planted categories found:", sorted(found & EXPECTED), "missed:", sorted(EXPECTED - found)
    )
    report = {
        "date_utc": datetime.now(UTC).isoformat(),
        "planted_copy": PLANTED,
        "note": "PLANTED copy written to show four anti-patterns; not model output",
        "expected_categories": sorted(EXPECTED),
        "result": result,
        "calls": [list(map(str, r)) for r in rows],
        "error": error,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"chunk_b_a7_planted_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print("report", path)
    return 0 if error is None else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
