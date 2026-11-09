"""Generate the evaluation's campaigns: the REAL run, on the registered evaluation database.

    PYTHONPATH=. uv run python -m scripts.evaluation_generate --wave round:1 --stop-after 3
    PYTHONPATH=. uv run python -m scripts.evaluation_generate --wave round:1
    PYTHONPATH=. uv run python -m scripts.evaluation_generate --verify-only

This is the driver `app/evaluation/generation.py` describes, pointed at `brandverse_evaluation`
(`config/evaluation_budget.yaml`) instead of the dry run's throwaway. What it adds over the dry-run
script, all of it the owner's instruction of 9 November:

* **`--stop-after N` (the CANARY).** Run N campaigns and stop, so every figure the analysis needs is
  read out of the database BEFORE 72 runs depend on it. The 0048 columns had never been written
  by a real evaluation run. `--verify-only` prints that read-out without running anything.
* **`--halt-on-invalidation` (default ON).** The driver re-runs an infrastructure failure by itself,
  up to the cap. The first time that happens for real the BATCH STOPS so the owner can see what the
  re-run produced: that path had only met fakes and a database, and it is the one that silently
  edits the dataset if it misbehaves.
* **One item at a time.** The wave is driven by repeated `generate(..., stop_after=1)` calls, which
  is the tested resume path: each call re-reads the checkpoint, skips what is settled and starts the
  next item. Nothing in `generation.py` changed to get this control.

REFUSALS BEFORE THE FIRST CALL, printed: the target must be the registered evaluation database on
loopback; the registered files must be committed and unmodified, so the registration provably
precedes the first call; the snapshot must hold every request of the wave; and the evaluation budget
must not already be spent.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg

REPO = Path(__file__).resolve().parents[2]
ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
REDIS_URL = "redis://127.0.0.1:6379/12"
SNAPSHOT = Path("C:/BrandverseData/evaluation-snapshot")
OUT_DIR = REPO / "evaluation" / "generation"
SCRATCH = Path(tempfile.gettempdir()) / "brandverse_generation"
CHECKPOINT = Path("C:/BrandverseData/evaluation-runs/generation-checkpoint.jsonl")

# The registration must precede the first call, provably: these are committed and unmodified or the
# run refuses (the same control as the stage-2 runner's).
REGISTERED_FILES = (
    "evaluation/run_order.yaml",
    "evaluation/test_set.yaml",
    "evaluation/brief_checks.json",
    "evaluation/brief_checks.lock",
    "config/scoring.yaml",
    "config/evaluation_budget.yaml",
    "config/prompts/versions.lock",
    "docs/evaluation-plan.md",
    "backend/app/evaluation/schedule.py",
)


def evaluation_database() -> str:
    """The one database the evaluation's calls may be made from (`evaluation_budget.yaml`)."""
    from app.llm.evaluation_budget import load_evaluation_budget

    names = sorted(load_evaluation_budget().databases)
    if len(names) != 1:
        raise SystemExit(f"the evaluation budget must name exactly one database, it names {names}")
    return names[0]


def target_url(database: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + database


def verify_target(url: str, database: str) -> dict[str, Any]:
    from app.evaluation.generation_runner import describe_target

    host, port, name = describe_target(url)
    print(f"TARGET  host={host} port={port} database={name}", flush=True)
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"REFUSED: host {host!r} is not loopback")
    if name != database:
        raise SystemExit(f"REFUSED: database is {name!r}, the registered one is {database!r}")
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("select current_database()").fetchone()
    if row is None or row[0] != database:
        raise SystemExit(f"REFUSED: the server says this is {row and row[0]!r}")
    print(f"VERIFIED the server answers as database={row[0]}", flush=True)
    return {"host": host, "port": port, "database": name}


def registration_problem() -> str | None:
    status = subprocess.run(  # noqa: S603 - fixed command
        ["git", "status", "--porcelain", "--", *REGISTERED_FILES],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"registered files are not committed:\n{status}" if status else None


def head_commit() -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def snapshot_problem(request_ids: list[str]) -> str | None:
    """Every request of the wave must be replayable: a miss is not an infrastructure kind, so it
    would end as the arm's own failure and be counted as a result."""
    committed = {
        json.loads(line)["request"]
        for line in (SNAPSHOT / "committed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(set(request_ids) - committed)
    return None if not missing else f"research is not recorded for {missing}"


def budget_state(url: str) -> tuple[int, str | None]:
    """(non-refused calls this database has logged, why no call may be made)."""
    from app.llm.evaluation_budget import load_evaluation_budget

    budget = load_evaluation_budget()
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute(
            "select count(*) filter (where outcome <> 'refused') from llm_calls"
        ).fetchone()
    made = int(row[0]) if row else 0
    refusal = budget.refusal(evaluation_database(), made)
    due = budget.report_due(made)
    return made, (refusal[1] if refusal else due)


def worker_env(url: str) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": url,
        "REDIS_URL": REDIS_URL,
        "ENVIRONMENT": "development",
        "EVALUATION_RUN": "true",
        "PIPELINE_AGENTS": "real",
        "RESEARCH_SNAPSHOT_DIR": str(SNAPSHOT),
        "RESEARCH_STEP_TIMEOUT_S": "900",
        "WORKER_JOB_TIMEOUT_S": "1800",
        "MAX_LLM_CALLS_PER_RUN": "20",
        # The strong tier is reachable for the regulated brand's step-11 claim check (T4). The
        # per-organisation cap cannot be used here: it requires the organisation to be is_practice
        # and no evaluation organisation is, so the 20/day provider limit is a SCHEDULING matter
        # (decision-log 2.58), not an enforced one.
        "LLM_ALLOW_STRONG_TIER": "true",
        "LLM_STRONG_TIER_ORG_ID": "",
        "PYTHONUNBUFFERED": "1",
    }


# ── what the canary must prove was stored ─────────────────────────────────────────────────────

FIGURES_SQL = """
select r.id, o.name, r.arm, r.status, r.depth, r.research_score,
       r.search_count, r.pages_fetched, r.pages_served,
       r.facts is not null as facts_stored,
       r.brief_check -> 'status' as brief_check_status,
       r.brief_check -> 'brief_sha256' as brief_sha,
       r.timings is not null as timings_stored,
       r.supersedes_run_id, r.llm_call_count, r.latency_ms,
       (select count(*) from campaigns c
        where c.run_id = r.id and c.strategy ? 'necessity') as necessity_records,
       (select count(*) from campaigns c
        where c.run_id = r.id and c.strategy -> 'injection' is not null) as injection_records,
       (select count(*) from run_waits w where w.run_id = r.id) as waits,
       (select count(*) from llm_calls l where l.run_id = r.id and l.outcome <> 'refused') as calls,
       (select count(*) from llm_calls l where l.run_id = r.id and l.tier = 'strong') as strong,
       (select count(*) from memory_items m where m.provenance = 'run:' || r.id::text) as memory,
       jsonb_array_length(r.declared_gaps) as gaps, r.failure
from runs r join organisations o on o.id = r.org_id
order by r.created_at
"""


def report_figures(url: str) -> list[dict[str, Any]]:
    with psycopg.connect(url, autocommit=True) as c:
        cur = c.execute(FIGURES_SQL)
        columns = [d[0] for d in cur.description or ()]
        rows = [dict(zip(columns, r, strict=True)) for r in cur.fetchall()]
    return rows


def print_figures(rows: list[dict[str, Any]]) -> list[str]:
    """Print every figure per run and return the complaints: a null that should not be null."""
    complaints: list[str] = []
    for row in rows:
        print(
            f"\n  run {row['id']}  {row['organisation'] if 'organisation' in row else row['name']}"
            f"  {row['arm']}  {row['status']}",
            flush=True,
        )
        for key in (
            "depth", "research_score", "search_count", "pages_fetched", "pages_served",
            "facts_stored", "brief_check_status", "timings_stored", "necessity_records",
            "injection_records", "waits", "calls", "strong", "memory", "gaps",
            "llm_call_count", "latency_ms", "supersedes_run_id", "failure",
        ):  # fmt: skip
            print(f"      {key:20} {row[key]}", flush=True)
        parked_or_done = row["status"] in ("awaiting_approval", "completed", "partial", "blocked")
        if not parked_or_done:
            continue
        needed = ["depth", "brief_check_status", "timings_stored", "facts_stored"]
        if row["arm"] != "baseline_a":  # Baseline A does no research at all
            needed += ["search_count", "pages_fetched", "pages_served"]
        if row["arm"] == "proposed":
            needed += ["research_score", "necessity_records"]
        for key in needed:
            if row[key] in (None, False, 0) and not (key == "search_count" and row[key] == 0):
                complaints.append(f"{row['arm']} run {row['id']}: {key} is {row[key]!r}")
        if row["status"] == "awaiting_approval" and not row["waits"]:
            complaints.append(f"{row['arm']} run {row['id']}: parked but no run_waits row")
    return complaints


# ── the run ───────────────────────────────────────────────────────────────────────────────────


async def drive(args: argparse.Namespace) -> int:
    from app.evaluation.generation import Checkpoint, GenerationStopped, generate, wave_items
    from app.evaluation.generation_runner import ApiRunner
    from app.evaluation.schedule import registered_order, test_set_requests
    from scripts.phase7_stage2 import WorkerSupervisor

    database = evaluation_database()
    url = target_url(database)
    positions = {r["id"]: r["position"] for r in test_set_requests()}
    items = wave_items(registered_order(), positions, args.wave)
    request_ids = sorted({i.request_id for i in items})
    print(f"WAVE {args.wave}: {len(items)} runs over {len(request_ids)} requests", flush=True)

    if (why := registration_problem()) is not None:
        print("REFUSED:", why, flush=True)
        return 1
    print(f"registration: committed and unmodified at {head_commit()}", flush=True)
    if (why := snapshot_problem(request_ids)) is not None:
        print("REFUSED:", why, flush=True)
        return 1
    print(f"snapshot: every request of the wave is replayable ({len(request_ids)})", flush=True)

    exists = False
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        exists = (
            c.execute("select 1 from pg_database where datname = %s", (database,)).fetchone()
            is not None
        )
    if not exists:
        if args.verify_only:
            print(f"{database} does not exist yet: nothing to verify", flush=True)
            return 1
        from scripts.e2e_prepare_db import main as prepare_database

        print(f"creating {database} from the real migrations", flush=True)
        prepared = prepare_database(ADMIN_URL, database)
        if prepared != url:
            print(f"REFUSED: prepared {prepared}, expected {url}", flush=True)
            return 1
    verify_target(url, database)

    made, refusal = budget_state(url)
    print(f"evaluation calls logged in this database: {made}", flush=True)
    if refusal is not None:
        print("REFUSED:", refusal, flush=True)
        return 1

    if args.verify_only:
        complaints = print_figures(report_figures(url))
        print("\nCOMPLAINTS:", complaints or "none", flush=True)
        return 0 if not complaints else 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    log_path = SCRATCH / f"worker_{stamp}.log"

    def launch(_n: int) -> tuple[Any, Path]:
        return (
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "app.workers"],
                env=worker_env(url),
                stdout=log_path.open("w", encoding="utf-8"),
                stderr=subprocess.STDOUT,
            ),
            log_path,
        )

    approver = uuid4()
    checkpoint = Checkpoint(CHECKPOINT)
    supervisor = WorkerSupervisor(launch, crash_log=SCRATCH / f"crashes_{stamp}.jsonl")
    if not await supervisor.start():
        print("worker did not become ready; log:", log_path, flush=True)
        return 1
    print(f"worker ready (log {log_path})", flush=True)
    watcher = asyncio.create_task(supervisor.watch(asyncio.current_task()))
    started_here: list[str] = []
    stopped: str | None = None
    try:
        import httpx
        from pydantic import SecretStr

        from app.api.main import create_app
        from app.core.config import get_settings
        from tests.auth_keys import TestIssuer

        settings = get_settings().model_copy(
            update={
                "database_url": SecretStr(url),
                "redis_url": REDIS_URL,
                "environment": "development",
                "pipeline_agents": "real",
                "run_events_block_ms": 200,
            }
        )
        issuer = TestIssuer()
        app = create_app(settings, verifier=issuer.verifier())
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=120
            ) as client:
                runner = ApiRunner(
                    client=client,
                    dsn=url,
                    token_for=issuer.token,
                    approver_id=approver,
                    scope=request_ids,
                    wait_timeout_s=2400.0,
                )
                while True:
                    # Only THIS wave's items: the checkpoint file carries every wave.
                    done_now = checkpoint.state()
                    settled = sum(
                        1 for i in items if (st := done_now.get(i.key)) is not None and st.done
                    )
                    if settled >= len(items):
                        break
                    if args.stop_after is not None and len(started_here) >= args.stop_after:
                        stopped = f"--stop-after {args.stop_after} reached"
                        break
                    before = set(checkpoint.state())
                    try:
                        report = await generate(items, runner, checkpoint, args.wave, stop_after=1)
                    except GenerationStopped as e:
                        stopped = f"GenerationStopped: {e}"
                        break
                    started_here += report.started
                    print(
                        f"  settled {settled + len(report.started) + len(report.reconciled)}"
                        f" of {len(items)}; started {report.started}, re-runs {report.reruns},"
                        f" reconciled {report.reconciled}",
                        flush=True,
                    )
                    if (
                        not report.started
                        and not report.reconciled
                        and before == set(checkpoint.state())
                    ):
                        stopped = "no progress in a pass: stopping rather than looping"
                        break
                    if args.halt_on_invalidation:
                        invalid = [
                            (key, attempt.infrastructure_failure)
                            for key, state in checkpoint.state().items()
                            for attempt in state.attempts
                            if attempt.outcome == "invalidated"
                        ]
                        if invalid:
                            stopped = (
                                "THE FIRST INFRASTRUCTURE INVALIDATION: "
                                f"{invalid}. Stopping so the re-run can be read before the rest "
                                "(owner, 9 November 2026)."
                            )
                            break
    finally:
        watcher.cancel()
        supervisor.stop()

    print("\nSTOPPED:", stopped or "the wave is complete", flush=True)
    print("worker crashes:", supervisor.crashes, flush=True)
    rows = report_figures(url)
    complaints = print_figures(rows)
    made_after, _ = budget_state(url)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.wave.replace(':', '')}_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "wave": args.wave,
                "commit": head_commit(),
                "stopped": stopped,
                "started_here": started_here,
                "calls_before": made,
                "calls_after": made_after,
                "approver_id": str(approver),
                "runs": rows,
                "complaints": complaints,
                "worker_crashes": supervisor.crashes,
            },
            indent=1,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"\ncalls: {made} -> {made_after} (this invocation spent {made_after - made})", flush=True
    )
    print("COMPLAINTS:", complaints or "none", flush=True)
    print("REPORT:", out, flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wave", default="round:1", choices=("round:1", "round:2", "round:3"))
    p.add_argument(
        "--stop-after", type=int, default=None, help="the canary: N campaigns, then stop"
    )
    p.add_argument("--verify-only", action="store_true", help="read the figures, run nothing")
    p.add_argument(
        "--no-halt-on-invalidation",
        dest="halt_on_invalidation",
        action="store_false",
        help="do NOT stop at the first infrastructure invalidation (the owner's rule says stop)",
    )
    args = p.parse_args()
    started = time.monotonic()
    try:
        return asyncio.run(drive(args), loop_factory=asyncio.SelectorEventLoop)
    finally:
        print(f"elapsed {time.monotonic() - started:.0f} s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
