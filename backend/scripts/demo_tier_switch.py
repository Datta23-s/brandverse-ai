"""Phases.md Phase 2 exit criterion, shown end to end: switching a tier's model is a one-line
config change. MOCKED provider — no real model call, no quota.

1. Copies config/models.yaml and edits ONE line: the cheap tier's `id:`. Prints the diff.
2. Makes one call through the real gateway and ledger with each registry, against its own
   throwaway database (`brandverse_tier_switch_demo`, created here from the real migrations), so
   its mocked rows never mix with the real-call tally in `brandverse_e2e`.
3. Prints the model string the provider received and the llm_calls rows: same code, same tier,
   different model.

Usage (from backend/):
  uv run python -m scripts.demo_tier_switch postgresql://postgres:...@127.0.0.1:55432/postgres
"""

import asyncio
import difflib
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import library_from
from app.llm.registry import ModelRegistry, Tier, load_registry
from scripts import e2e_prepare_db
from scripts.llm_smoke import CapitalCity, prepare_run
from tests.llm_fakes import FakeCompletion, reply

DEMO_DB = "brandverse_tier_switch_demo"
SWITCHED_ID = "mock-switched-model"  # never sent anywhere: the provider is mocked


async def one_call(url: str, registry: ModelRegistry, ctx: CallContext, label: str) -> str:
    settings = get_settings()
    completion = FakeCompletion(
        reply('{"country": "Portugal", "capital": "Lisbon", "confidence": 1}')
    )
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    try:
        ledger = CallLedger(
            pool, registry, max_calls_per_run=10, max_cost_per_run_usd=1, pending_expiry_s=900
        )
        prompts = library_from(settings.prompts_dir)
        gateway = LLMGateway(
            registry,
            ledger,
            prompts,
            api_key="mock",
            timeout_s=5,
            max_rate_limit_waits=0,
            max_rate_limit_wait_s=1,
            completion=completion,
        )
        await gateway.structured(
            CapitalCity,
            tier=Tier.CHEAP,
            prompt=prompts.get("smoke_capital_city", "v1").render(country="Portugal"),
            ctx=ctx,
            route_reason=f"tier-switch demo ({label}), mocked provider",
            max_output_tokens=128,
        )
    finally:
        await pool.close()
    return str(completion.calls[0]["model"])


async def main(admin_url: str) -> int:
    url = e2e_prepare_db.main(admin_url, DEMO_DB)  # refuses a managed Supabase host
    models_yaml = get_settings().models_config_path
    original = models_yaml.read_text(encoding="utf-8")
    before = load_registry(models_yaml)
    current = before.model(Tier.CHEAP).id
    switched = original.replace(f"id: {current}", f"id: {SWITCHED_ID}", 1)

    changed = [
        line
        for line in difflib.unified_diff(original.splitlines(), switched.splitlines(), lineterm="")
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    print("config/models.yaml, the whole change:")
    for line in changed:
        print(f"  {line}")
    if len(changed) != 2:
        print("FAILED: expected exactly one line out and one line in")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "models.yaml"
        path.write_text(switched, encoding="utf-8")
        after = load_registry(path)

    ctx = prepare_run(url)
    sent_before = await one_call(url, before, ctx, "before")
    sent_after = await one_call(url, after, ctx, "after")
    print(f"provider received, before: {sent_before}")
    print(f"provider received, after:  {sent_after}")
    untouched = all(before.model(t) == after.model(t) for t in (Tier.STANDARD, Tier.STRONG))
    print(f"standard and strong tiers unchanged: {untouched}")

    with psycopg.connect(url) as c:
        rows = c.execute(
            "select id, tier, model, prompt_id, prompt_version, outcome, cost_usd "
            "from llm_calls where run_id = %s order by id",
            (ctx.run_id,),
        ).fetchall()
    print(f"llm_calls ({DEMO_DB}):")
    for r in rows:
        print(f"  {r}")
    ok = (
        sent_before == f"gemini/{current}"
        and sent_after == f"gemini/{SWITCHED_ID}"
        and untouched
        and [r[2] for r in rows] == [current, SWITCHED_ID]
    )
    print("RESULT:", "one-line switch confirmed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1]), loop_factory=asyncio.SelectorEventLoop))
