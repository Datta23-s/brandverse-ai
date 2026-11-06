"""Which registered models answer RIGHT NOW, one call each (owner, 6 November 2026).

    PYTHONPATH=. uv run python -m scripts.provider_probe --tiers cheap,standard
    PYTHONPATH=. uv run python -m scripts.provider_probe --tiers strong --allow-strong

Answers one question and no other: is a failure the MODEL's (one model 503s, another answers) or
the PROVIDER's (every model 503s)? It makes exactly one call per tier, with retries switched OFF, so
each tier costs one row and the result is a clean yes or no.

WHY THE NETWORK IS CHECKED FIRST, and why that check refuses rather than warns. Overnight on
5 November ten interim attempts and about fifty calls were spent on `getaddrinfo failed` — this
laptop could not resolve `generativelanguage.googleapis.com` — while the logs read as "the agents
failed". A host that cannot resolve a name cannot tell you anything about a provider, and a probe
that reported "every model is down" from a local DNS fault would be worse than no probe: it would
argue for changing the provider. So the probe resolves the host and opens a socket to it first, and
if either fails it spends NOTHING and says the host is the problem.

The models come from `config/models.yaml` (Rules.md P5: the only place a model ID lives) and the
calls go through the gateway (P6), so the development ceiling counts them like any other call. The
strong tier is refused unless `--allow-strong` is passed: it has 20 requests a day and the
evaluation needs them.
"""

import argparse
import asyncio
import socket
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CapitalCity(BaseModel):
    """The probe's schema, at module level so tests/test_llm_request_body.py can see it and check
    that it is sent in the provider-safe form like every other structured request."""

    country: str
    city: str


API_HOST = "generativelanguage.googleapis.com"
API_PORT = 443
TIERS = ("cheap", "standard", "strong")


def host_unreachable(host: str = API_HOST, port: int = API_PORT) -> str | None:
    """Why no call should be attempted from this machine, or None. Costs nothing."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return (
            f"{host} does not resolve from this machine ({e}): "
            "the HOST is the problem, not the provider"
        )
    if not infos:
        return f"{host} resolved to no address: the HOST is the problem, not the provider"
    family, socktype, proto, _canon, sockaddr = infos[0]
    try:
        with socket.socket(family, socktype, proto) as s:
            s.settimeout(10)
            s.connect(sockaddr)
    except OSError as e:
        return (
            f"{host}:{port} refused a connection from this machine ({e}): the HOST is the problem"
        )
    return None


async def probe(database_url: str, tiers: list[str], allow_strong: bool) -> int:
    from psycopg_pool import AsyncConnectionPool

    from app.core.config import get_settings
    from app.core.infrastructure import classify_infrastructure_failure
    from app.llm.dev_budget import load_dev_budget
    from app.llm.gateway import LLMGateway, is_provider_unavailable
    from app.llm.ledger import CallLedger
    from app.llm.prompts import library_from
    from app.llm.registry import Tier, registry_from
    from scripts.llm_smoke import prepare_run
    from tests.db.target_guard import managed_supabase_hosts

    settings = get_settings()
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set")
        return 1
    if managed_supabase_hosts(database_url):
        print("refusing to log a probe to the live project")
        return 1

    if (why := host_unreachable()) is not None:
        print(f"NO CALL MADE: {why}")
        print("Fix the network first; a probe from here would say nothing about the provider.")
        return 2

    print(f"{API_HOST}:{API_PORT} resolves and accepts a connection; probing {len(tiers)} tier(s)")
    registry = registry_from(settings.models_config_path)
    prompts = library_from(settings.prompts_dir)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        database_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    results: dict[str, str] = {}
    try:
        for name in tiers:
            tier = Tier(name)
            model = registry.model(tier)
            ledger = CallLedger(
                pool,
                registry,
                max_calls_per_run=1,
                max_cost_per_run_usd=0.05,
                pending_expiry_s=settings.llm_pending_expiry_s,
                # The probe spends real calls, so it counts against the development ceiling like
                # anything else: a gateway on the real provider refuses to be built without it.
                dev_budget=load_dev_budget(),
            )
            gateway = LLMGateway(
                registry,
                ledger,
                prompts,
                api_key=settings.google_api_key.get_secret_value(),
                timeout_s=45,
                max_rate_limit_waits=0,
                max_rate_limit_wait_s=1,
                unavailable_retries=0,  # one call per model: the probe is the measurement
                circuit_breaker_streak=1,
                unavailable_total_cap=1,
                allow_strong_tier=allow_strong,
            )
            ctx = prepare_run(database_url)
            try:
                answer = await gateway.structured(
                    CapitalCity,
                    tier=tier,
                    prompt=prompts.get("smoke_capital_city", "v1").render(country="Portugal"),
                    ctx=ctx,
                    route_reason=f"provider probe {name}: which models answer (owner, 2026-11-06)",
                    max_output_tokens=128,
                )
                results[name] = f"ANSWERED  {model.id}  -> {answer.city}"
            except Exception as e:  # reported, never retried: the failure IS the result
                kind = classify_infrastructure_failure(e) or "not an infrastructure failure"
                unavailable = " (5xx)" if is_provider_unavailable(e) else ""
                results[name] = f"FAILED    {model.id}  -> {type(e).__name__}{unavailable}: {kind}"
    finally:
        await pool.close()

    print()
    for name in tiers:
        print(f"  {name:9} {results.get(name, 'not probed')}")
    answered = [n for n, r in results.items() if r.startswith("ANSWERED")]
    print()
    if answered:
        print(f"VERDICT: the provider is reachable and serving: {', '.join(answered)} answered.")
    else:
        print("VERDICT: every tier probed FAILED, from a machine that can reach the API host.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--database-url",
        default="postgresql://postgres:postgres@127.0.0.1:55432/brandverse_stage2",
    )
    p.add_argument("--tiers", default="cheap,standard")
    p.add_argument("--allow-strong", action="store_true", help="spend one of the 20 strong calls")
    a = p.parse_args()
    tiers = [t.strip() for t in a.tiers.split(",") if t.strip()]
    for t in tiers:
        if t not in TIERS:
            p.error(f"unknown tier {t!r}; choose from {TIERS}")
    if "strong" in tiers and not a.allow_strong:
        p.error("the strong tier has 20 requests a day: pass --allow-strong to spend one")
    return asyncio.run(
        probe(a.database_url, tiers, a.allow_strong), loop_factory=asyncio.SelectorEventLoop
    )


if __name__ == "__main__":
    sys.exit(main())
