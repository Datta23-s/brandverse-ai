"""Phase 6 chunk F: the FIRST real campaign, produced and approved through the product's own path.

Everything the e2e test exercises with mock agents, here with the real ones: `POST /api/v1/runs`
(the audience carried), the worker running the REAL composition (`pipeline_agents=real`), research
REPLAYED from the evaluation snapshot (zero live searches or fetches, so the recording is
untouched; owner, 2026-11-01), the run parking at the approval step, `GET .../campaign`, an
approver's `POST .../approve`, the continuation, and write-back. Only the model calls are live.

- Request R10 (B04 product A, footwear, non-regulated), depth D1, STANDARD tier only (the strong
  tier is never enabled: `llm_strong_tier_org_id` stays unset, `llm_allow_strong_tier` false).
- Logs to a THROWAWAY local database (`brandverse_chunk_f`, recreated), never the live project;
  the live ledger cannot see these calls, so they are tallied by hand in Architecture.md section 4.
- The run's call cap is `--max-calls` (at most 10, enforced by the ledger). No retry loop.
- The decision's `kind` in the throwaway database is `calibration`: the API derives `tuning` only
  for the organisation named by `llm_strong_tier_org_id`, which stays unset. Nothing here is the
  real calibration record, which would live on the live project.

Prints the call tally from `llm_calls` (per call: agent, step, tier, tokens, latency, the
rate-limit wait apart from latency), the run row (`latency_ms` is wall time minus the approval
wait) and what write-back stored.

Usage (from backend/):  PYTHONPATH=. uv run python -m scripts.chunk_f_first_campaign
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from pydantic import SecretStr

from app.api.main import create_app
from app.core.config import get_settings
from scripts.chunk_b_d1_replay import STORE, request_inputs, seed
from scripts.e2e_prepare_db import main as prepare_database
from tests.auth_keys import TestIssuer
from tests.db.target_guard import managed_supabase_hosts

ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_chunk_f"
REDIS_URL = "redis://127.0.0.1:6379/14"
MAX_CALLS = 10
PARK_TIMEOUT_S = 900
END_TIMEOUT_S = 300


async def main(args: argparse.Namespace) -> int:
    base = get_settings()
    if base.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    if not 1 <= args.max_calls <= MAX_CALLS:
        print(f"--max-calls must be 1 to {MAX_CALLS}")
        return 1
    if not os.path.exists(STORE):  # noqa: ASYNC240 - one stat before anything starts
        print(f"no snapshot store at {STORE}")
        return 1
    url = prepare_database(ADMIN_URL, DB_NAME)
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    inp = request_inputs()
    org, product, _spare_run, user = seed(url, inp)
    with psycopg.connect(url, autocommit=True) as c:
        for role in ("editor", "approver"):
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, user, role),
            )
    settings = base.model_copy(
        update={
            "database_url": SecretStr(url),
            "redis_url": REDIS_URL,
            "environment": "development",
            "pipeline_agents": "real",
            "research_snapshot_dir": STORE,
            "max_llm_calls_per_run": args.max_calls,
            "llm_allow_strong_tier": False,
            "llm_strong_tier_org_id": None,
            "run_events_block_ms": 200,
        }
    )
    issuer = TestIssuer()
    headers = {"Authorization": f"Bearer {issuer.token(user)}"}
    app = create_app(settings, verifier=issuer.verifier())
    run_id: UUID | None = None
    # The worker is its own PROCESS, as in production: it loads the 1 GB encoder synchronously,
    # which would starve the API's database pool if both shared one event loop.
    worker_log = Path(tempfile.gettempdir()) / "chunk_f_worker.log"
    worker = subprocess.Popen(  # noqa: S603, ASYNC220 - a fixed command, started once
        [sys.executable, "-m", "app.workers"],
        env={
            **os.environ,
            "DATABASE_URL": url,
            "REDIS_URL": REDIS_URL,
            "ENVIRONMENT": "development",
            "PIPELINE_AGENTS": "real",
            "RESEARCH_SNAPSHOT_DIR": str(STORE),
            "MAX_LLM_CALLS_PER_RUN": str(args.max_calls),
            "LLM_ALLOW_STRONG_TIER": "false",
            "LLM_STRONG_TIER_ORG_ID": "",
        },
        stdout=worker_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(90):  # the encoder takes about 40 s to load
        await asyncio.sleep(2)
        if "pipeline ready" in worker_log.read_text(encoding="utf-8", errors="replace"):
            break
        if worker.poll() is not None:
            print("the worker exited at start-up:", worker_log.read_text(errors="replace")[-800:])
            return 1
    else:
        print("the worker did not report 'pipeline ready' in 3 minutes")
        worker.terminate()
        return 1
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=60
            ) as client:
                r = await client.post(
                    "/api/v1/runs",
                    json={
                        "org_id": str(org),
                        "product_id": str(product),
                        "brief": inp["brief"],
                        "audience": inp["audience"],
                        "depth": "D1",
                    },
                    headers=headers,
                )
                print("POST /api/v1/runs ->", r.status_code)
                if r.status_code != 202:
                    print(r.text)
                    return 1
                run_id = UUID(r.json()["run_id"])
                view = await wait_for_campaign(client, run_id, headers)
                if view is None:
                    return report(url, run_id, "never parked")
                print_view(view)
                d = await client.post(
                    f"/api/v1/runs/{run_id}/approve",
                    json={"reason": None, "failed_gate": None, "seconds_spent": 30},
                    headers=headers,
                )
                print("POST approve ->", d.status_code, d.json() if d.status_code < 500 else "")
                await wait_for_end(url, run_id)
    finally:
        worker.terminate()
        worker.wait(timeout=30)
    return report(url, run_id, None)


async def wait_for_campaign(
    client: httpx.AsyncClient, run_id: UUID, headers: dict[str, str]
) -> dict[str, object] | None:
    for _ in range(PARK_TIMEOUT_S // 3):
        await asyncio.sleep(3)
        r = await client.get(f"/api/v1/runs/{run_id}/campaign", headers=headers)
        if r.status_code == 200:
            body: dict[str, object] = r.json()
            return body
    return None


async def wait_for_end(url: str, run_id: UUID) -> None:
    for _ in range(END_TIMEOUT_S // 2):
        await asyncio.sleep(2)
        with psycopg.connect(url, autocommit=True) as c:
            row = c.execute("select status from runs where id = %s", (run_id,)).fetchone()
        if row is not None and row[0] not in ("queued", "awaiting_approval"):
            return


def print_view(view: dict[str, object]) -> None:
    parts = view["copy_parts"]
    findings = view["findings"]
    if not (isinstance(parts, dict) and isinstance(findings, list)):
        raise TypeError("unexpected campaign view")
    print(f"parked: gates={view['gate_outcome']} round={view['revision_round']}")
    for k, v in parts.items():
        print(f"  {k}: {v}")
    for f in findings:
        print(f"  finding {f['outcome']} {f['gate']}/{f['kind']}: {f['message']}")
    print("  advisory in the view:", "advisory" in str(view).lower())


def report(url: str, run_id: UUID | None, problem: str | None) -> int:
    if problem:
        print("PROBLEM:", problem)
    if run_id is None:
        return 1
    with psycopg.connect(url, autocommit=True) as c:
        calls = c.execute(
            "select id, agent_id, step, revision_round, tier, outcome, input_tokens, "
            "output_tokens, latency_ms, rate_limit_wait_ms, cost_usd "
            "from llm_calls where run_id = %s order by id",
            (run_id,),
        ).fetchall()
        run = c.execute(
            "select status, llm_call_count, total_cost_usd, latency_ms, declared_gaps, depth, "
            "research_score from runs where id = %s",
            (run_id,),
        ).fetchone()
        waits = c.execute(
            "select extract(epoch from (resumed_at - parked_at)) from run_waits where run_id = %s",
            (run_id,),
        ).fetchall()
        memory = c.execute(
            "select kind, length(content), source_score, confidence, evidence_first_party_only "
            "from memory_items order by created_at"
        ).fetchall()
        decisions = c.execute(
            "select a.outcome, a.kind, a.revision_round, a.chain_seq is not null from approvals a "
            "join campaigns c on c.id = a.campaign_id where c.run_id = %s",
            (run_id,),
        ).fetchall()
    real = [r for r in calls if r[5] != "refused"]
    print(f"\nllm_calls rows: {len(calls)}; real calls made: {len(real)}")
    standard = sum(1 for r in real if r[4] == "standard")
    cheap = sum(1 for r in real if r[4] == "cheap")
    strong = sum(1 for r in real if r[4] == "strong")
    print(
        f"TALLY for Architecture.md section 4: cheap {cheap}, standard {standard}, strong {strong}"
    )
    for r in calls:
        print(
            "  ",
            f"{r[1]} step={r[2]} round={r[3]} tier={r[4]} {r[5]} in={r[6]} out={r[7]} "
            f"latency_ms={r[8]} rate_limit_wait_ms={r[9]} cost={r[10]}",
        )
    print("run row:", run)
    print("approval waits (s):", waits)
    print("memory items written:", memory)
    print("decisions:", decisions)
    return 0 if problem is None else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    # psycopg's async mode refuses Windows' default ProactorEventLoop (Architecture.md section 11).
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
