"""Phase 6 chunk F: the killed-worker run, on REAL models (owner, 2026-11-01).

If the worker dies at campaign 60 of 100, does the run finish without a person? One real campaign
(R10 at D1, research REPLAYED from the snapshot, STANDARD tier only) is started through the API.
When at least two model calls have completed, the worker PROCESS is killed outright
(`Popen.kill`, the force-kill that Windows gives a process), a second worker is started, and its
start-up sweep (`app/workers/recovery.py`) queues a `recover_pipeline` job for the run the first
one left. Nothing else touches the run until it parks; then an approver approves it.

Reports what a reader needs to judge the recovery: the status the run was left in, whether the
second worker continued it, every `llm_calls` row (a step re-run from its checkpoint appears again,
told apart by `execution_id`), the total against an undisturbed run (7), and the run row.

- STANDARD tier only (`llm_strong_tier_org_id` unset, `llm_allow_strong_tier` false). The run's
  call cap is `--max-calls` (at most 14: the undisturbed 7 plus a repeated step).
- Logs to a THROWAWAY database (`brandverse_chunk_f_kill`, recreated), never the live project.

Usage (from backend/):  PYTHONPATH=. uv run python -m scripts.chunk_f_killed_worker
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
from scripts.chunk_f_first_campaign import print_view, report, wait_for_campaign, wait_for_end
from scripts.e2e_prepare_db import main as prepare_database
from tests.auth_keys import TestIssuer
from tests.db.target_guard import managed_supabase_hosts

ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_chunk_f_kill"
REDIS_URL = "redis://127.0.0.1:6379/13"
MAX_CALLS = 14
KILL_AFTER_OK_CALLS = 2


def start_worker(url: str, cap: int, log: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - a fixed command
        [sys.executable, "-m", "app.workers"],
        env={
            **os.environ,
            "DATABASE_URL": url,
            "REDIS_URL": REDIS_URL,
            "ENVIRONMENT": "development",
            "PIPELINE_AGENTS": "real",
            "RESEARCH_SNAPSHOT_DIR": str(STORE),
            "MAX_LLM_CALLS_PER_RUN": str(cap),
            "LLM_ALLOW_STRONG_TIER": "false",
            "LLM_STRONG_TIER_ORG_ID": "",
        },
        stdout=log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )


async def wait_ready(worker: subprocess.Popen[bytes], log: Path) -> bool:
    for _ in range(90):  # the encoder takes about 40 s to load
        await asyncio.sleep(2)
        if "pipeline ready" in log.read_text(  # noqa: ASYNC240 - a small log file, polled
            encoding="utf-8", errors="replace"
        ):
            return True
        if worker.poll() is not None:
            return False
    return False


def ok_calls(url: str, run_id: UUID) -> int:
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute(
            "select count(*) from llm_calls where run_id = %s and outcome = 'ok'", (run_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def run_status(url: str, run_id: UUID) -> str | None:
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("select status from runs where id = %s", (run_id,)).fetchone()
    return None if row is None else str(row[0])


async def main(args: argparse.Namespace) -> int:
    base = get_settings()
    if base.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    if not 1 <= args.max_calls <= MAX_CALLS:
        print(f"--max-calls must be 1 to {MAX_CALLS}")
        return 1
    url = prepare_database(ADMIN_URL, DB_NAME)
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    inp = request_inputs()
    org, product, _spare, user = seed(url, inp)
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
    log1 = Path(tempfile.gettempdir()) / "chunk_f_kill_worker1.log"
    log2 = Path(tempfile.gettempdir()) / "chunk_f_kill_worker2.log"
    first = start_worker(url, args.max_calls, log1)
    second: subprocess.Popen[bytes] | None = None
    run_id: UUID | None = None
    try:
        if not await wait_ready(first, log1):
            print("worker 1 did not come up:", log1.read_text(errors="replace")[-600:])
            return 1
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
                    return 1
                run_id = UUID(r.json()["run_id"])
                for _ in range(600):  # up to 5 minutes
                    await asyncio.sleep(0.5)
                    if ok_calls(url, run_id) >= KILL_AFTER_OK_CALLS:
                        break
                made = ok_calls(url, run_id)
                status_before = run_status(url, run_id)
                first.kill()  # the force-kill: nothing is written by the dying process
                first.wait(timeout=30)
                print(
                    f"KILLED worker 1 with {made} model calls complete; run status {status_before}"
                )
                second = start_worker(url, args.max_calls, log2)
                if not await wait_ready(second, log2):
                    print("worker 2 did not come up:", log2.read_text(errors="replace")[-600:])
                    return 1
                print("worker 2 is up; its start-up sweep should queue the recovery")
                view = await wait_for_campaign(client, run_id, headers)
                if view is None:
                    print("the run never parked after the worker was replaced")
                    return report(url, run_id, "never parked after the kill")
                print_view(view)
                d = await client.post(
                    f"/api/v1/runs/{run_id}/approve",
                    json={"reason": None, "failed_gate": None, "seconds_spent": 30},
                    headers=headers,
                )
                print("POST approve ->", d.status_code)
                await wait_for_end(url, run_id)
    finally:
        for w in (first, second):
            if w is not None and w.poll() is None:
                w.terminate()
                w.wait(timeout=30)
    print("worker 2 log mentions the sweep:", "queued recovery" in log2.read_text(errors="replace"))
    return report(url, run_id, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    # psycopg's async mode refuses Windows' default ProactorEventLoop (Architecture.md section 11).
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
