"""The harness dry run (decision-log 2.48 item 4, 2.50): two requests across all three arms, on a
THROWAWAY database, through the real API, worker and database.

What it exercises, in order (every step prints what it saw; the JSON report keeps it):
  1. the target: printed, then refused unless it is the throwaway on loopback, BEFORE any call;
  2. the database: created from the real migrations, its ledger scope registered through a TEMPORARY
     budget file (config/evaluation_budget.yaml is never written and is checked unchanged);
  3. the baselines wave, run by a CHILD process that is KILLED while a Baseline B run is in flight,
     then resumed: the checkpoint reconciles the run the dead harness left;
  4. Proposed round 2 REFUSED while round 1 is undecided (the serialising rule), before and after
     round 1 has been generated;
  5. the harness cannot approve (the submitter is refused 403); a STAND-IN approver decides round 1
     ONLY HERE, in the throwaway; the real evaluation has a human (Rules.md T6);
  6. Proposed round 2, generated after the decision, reads the memory round 1's approval wrote;
  7. the checks that cost no call: arms cannot reach each other's memory, all arms of a request
     carry the same frozen brief check, the tally per database.

Usage (from backend/, PowerShell or bash):  PYTHONPATH=. uv run python -m scripts.evaluation_dry_run
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
from uuid import UUID, uuid4

import psycopg
import yaml

THROWAWAY = "brandverse_dryrun_eval"
ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
REDIS_URL = "redis://127.0.0.1:6379/13"
STORE = Path("C:/BrandverseData/evaluation-snapshot")
REPO = Path(__file__).resolve().parents[2]
REAL_BUDGET = REPO / "config" / "evaluation_budget.yaml"
OUT_DIR = REPO / "evaluation" / "dry_run"
SCOPE = ("R01", "R02")  # B01 positions 1 and 2, both recorded in the snapshot
DRY_RUN_CEILING = 80  # the most calls this dry run may make: a bound, not an estimate
SCRATCH = Path(tempfile.gettempdir()) / "brandverse_dryrun"


def throwaway_url() -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + THROWAWAY


def write_temp_budget(path: Path) -> None:
    real = yaml.safe_load(REAL_BUDGET.read_text(encoding="utf-8"))
    real.update(
        ceiling_calls=DRY_RUN_CEILING,
        base_calls=DRY_RUN_CEILING - 20,
        rerun_allowance_calls=20,
        report_to_owner_at_calls=DRY_RUN_CEILING - 5,
        databases=[THROWAWAY],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# TEMPORARY budget for the dry run only (decision-log 2.50). Never committed.\n"
        + yaml.safe_dump(real),
        encoding="utf-8",
    )


def verify_target(url: str) -> dict[str, Any]:
    """Print the target, refuse unless it is the throwaway, then ASK THE SERVER which database it
    reached: the URL is what we meant, `current_database()` is what we got."""
    from app.evaluation.generation_runner import describe_target, target_refusal

    host, port, database = describe_target(url)
    print(f"TARGET  host={host} port={port} database={database}", flush=True)
    if (why := target_refusal(url, THROWAWAY)) is not None:
        print("REFUSED:", why, flush=True)
        raise SystemExit(1)
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("select current_database(), inet_server_port()").fetchone()
    if row is None or row[0] != THROWAWAY:
        print(f"REFUSED: the server says this is {row and row[0]!r}", flush=True)
        raise SystemExit(1)
    print(f"VERIFIED the server answers as database={row[0]} (container port {row[1]})", flush=True)
    return {"host": host, "port": port, "database": database}


def worker_env(url: str, budget: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": url,
        "REDIS_URL": REDIS_URL,
        "ENVIRONMENT": "development",
        "EVALUATION_RUN": "true",
        "PIPELINE_AGENTS": "real",
        "RESEARCH_SNAPSHOT_DIR": str(STORE),
        "MAX_LLM_CALLS_PER_RUN": "20",
        "LLM_ALLOW_STRONG_TIER": "false",
        "LLM_STRONG_TIER_ORG_ID": "",
        "EVALUATION_BUDGET_PATH": str(budget),
        "PYTHONUNBUFFERED": "1",
        "WORKER_JOB_TIMEOUT_S": "1800",
        "RESEARCH_STEP_TIMEOUT_S": "900",
    }


async def build(url: str, approver_id: UUID):  # type: ignore[no-untyped-def]
    """The in-process API app. Imported late so the pure parts load fast."""
    import httpx
    from pydantic import SecretStr

    from app.api.main import create_app
    from app.core.config import get_settings
    from app.evaluation.generation_runner import ApiRunner
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
    return app, issuer, httpx, ApiRunner, approver_id


async def with_runner(url: str, approver_id: UUID, body):  # type: ignore[no-untyped-def]
    app, issuer, httpx, ApiRunner, _ = await build(url, approver_id)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as cl:
            runner = ApiRunner(
                client=cl,
                dsn=url,
                token_for=issuer.token,
                approver_id=approver_id,
                scope=SCOPE,
                wait_timeout_s=1500.0,
            )
            return await body(runner, cl, issuer)


def scoped_items(wave: str):  # type: ignore[no-untyped-def]
    from app.evaluation.generation import wave_items
    from app.evaluation.schedule import registered_order, test_set_requests

    positions = {r["id"]: r["position"] for r in test_set_requests()}
    return [i for i in wave_items(registered_order(), positions, wave) if i.request_id in SCOPE]  # type: ignore[arg-type]


async def run_wave_here(url: str, approver_id: UUID, wave: str, checkpoint: Path, stop_after=None):  # type: ignore[no-untyped-def]
    from app.evaluation.generation import Checkpoint, generate

    async def body(runner, _client, _issuer):  # type: ignore[no-untyped-def]
        return await generate(
            scoped_items(wave),
            runner,
            Checkpoint(checkpoint),
            wave,  # type: ignore[arg-type]
            stop_after=stop_after,
        )

    return await with_runner(url, approver_id, body)


def child_main(a: argparse.Namespace) -> int:
    """The harness process the parent kills. It does exactly what a normal invocation does."""
    verify_target(a.url)
    asyncio.run(
        run_wave_here(a.url, UUID(a.approver), a.wave, Path(a.checkpoint)),
        loop_factory=asyncio.SelectorEventLoop,
    )
    return 0


def checkpoint_events(path: Path) -> list[dict[str, Any]]:
    from app.evaluation.generation import Checkpoint

    return Checkpoint(path).events()


def kill_child_mid_run(
    url: str, approver: UUID, checkpoint: Path, wave: str, give_up_s: float = 900.0
) -> dict[str, Any]:
    """Start the harness as a child; kill it as soon as a Baseline B run is in flight (a `start`
    line with no `end`). Returns what the checkpoint held at the kill."""
    cmd = [
        sys.executable, "-m", "scripts.evaluation_dry_run", "--child", "--wave", wave,
        "--url", url, "--approver", str(approver), "--checkpoint", str(checkpoint),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO / "backend", env={**os.environ, "PYTHONPATH": "."})  # noqa: S603
    t0 = time.monotonic()
    killed: dict[str, Any] = {"killed": False}
    while time.monotonic() - t0 < give_up_s and proc.poll() is None:
        events = checkpoint_events(checkpoint)
        ended = {(e["item"], e["attempt"]) for e in events if e["event"] == "end"}
        flying = [
            e for e in events
            if e["event"] == "start" and (e["item"], e["attempt"]) not in ended
        ]  # fmt: skip
        target = [e for e in flying if e["item"].endswith(":baseline_b")]
        if target:
            proc.kill()
            proc.wait()
            killed = {"killed": True, "in_flight": target[0]["item"], "run_id": target[0]["run_id"]}
            break
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
    killed["events_at_kill"] = len(checkpoint_events(checkpoint))
    return killed


def evidence(url: str) -> dict[str, Any]:
    """Everything the report says, read from the database after the fact; no model call."""
    with psycopg.connect(url, autocommit=True) as c:
        runs = c.execute(
            "select r.id, o.name, r.arm, r.status, r.depth, r.research_score, r.llm_call_count, "
            "r.declared_gaps, r.failure, r.brief_check, r.request->>'brief', r.created_at "
            "from runs r join organisations o on o.id = r.org_id order by r.created_at"
        ).fetchall()
        memory = c.execute(
            "select o.name, count(*) from memory_items m join organisations o on o.id = m.org_id "
            "group by 1 order by 1"
        ).fetchall()
        calls = c.execute(
            "select tier, outcome, count(*) from llm_calls group by 1, 2 order by 1, 2"
        ).fetchall()
        by_prompt = c.execute(
            "select r.arm, l.prompt_id, count(*) from llm_calls l join runs r on r.id = l.run_id "
            "where l.outcome <> 'refused' group by 1, 2 order by 1, 2"
        ).fetchall()
        refused = c.execute("select count(*) from llm_calls where outcome = 'refused'").fetchone()
        decisions = c.execute(
            "select r.id, a.outcome, a.reason from approvals a join campaigns cp on "
            "cp.id = a.campaign_id join runs r on r.id = cp.run_id order by a.decided_at"
        ).fetchall()
    return {
        "runs": [
            {
                "run_id": str(r[0]),
                "organisation": r[1],
                "arm": r[2],
                "status": r[3],
                "depth": r[4],
                "r": None if r[5] is None else float(r[5]),
                "calls": r[6],
                "declared_gaps": r[7],
                "failure": r[8],
                "brief_check_status": (r[9] or {}).get("status"),
                "brief_check_sha": (r[9] or {}).get("brief_sha256"),
                "brief": r[10],
                "created_at": r[11].isoformat(),
            }
            for r in runs
        ],  # fmt: skip
        "memory_items_by_organisation": {str(n): int(k) for n, k in memory},
        "llm_calls_by_tier_and_outcome": [[str(t), str(o), int(n)] for t, o, n in calls],
        "llm_calls_by_arm_and_prompt": [[str(a), str(p), int(n)] for a, p, n in by_prompt],
        "refused_calls": int(refused[0]) if refused else 0,
        "decisions": [[str(r), str(o), r2] for r, o, r2 in decisions],
    }


async def main_async() -> int:
    from app.evaluation.generation import GenerationStopped
    from scripts.e2e_prepare_db import main as prepare_database
    from scripts.phase7_stage2 import WorkerSupervisor

    findings: list[str] = []
    steps: dict[str, Any] = {}
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    url = throwaway_url()
    real_budget_before = REAL_BUDGET.read_bytes()

    # The NAME is checked before the database is created; the SERVER is asked after.
    from app.evaluation.generation_runner import target_refusal

    print(f"TARGET (to be created) {url}", flush=True)
    if (why := target_refusal(url, THROWAWAY)) is not None:
        print("REFUSED:", why, flush=True)
        return 1
    prepared = prepare_database(ADMIN_URL, THROWAWAY)
    if prepared != url:
        print(f"REFUSED: prepared {prepared}, expected {url}", flush=True)
        return 1
    steps["target"] = verify_target(url)

    budget = SCRATCH / f"evaluation_budget_dryrun_{stamp}.yaml"
    write_temp_budget(budget)
    print(
        f"temporary budget: {budget} (ceiling {DRY_RUN_CEILING}, database {THROWAWAY})", flush=True
    )

    approver = uuid4()
    checkpoint = SCRATCH / f"checkpoint_{stamp}.jsonl"
    log_path = SCRATCH / f"worker_{stamp}.log"

    def launch(_n: int):  # type: ignore[no-untyped-def]
        return (
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "app.workers"],
                env=worker_env(url, budget),
                stdout=log_path.open("w", encoding="utf-8"),
                stderr=subprocess.STDOUT,
            ),
            log_path,
        )

    supervisor = WorkerSupervisor(launch, crash_log=SCRATCH / f"crashes_{stamp}.jsonl")
    if not await supervisor.start():
        print("worker did not become ready; log:", log_path, flush=True)
        findings.append(f"worker did not start: see {log_path}")
        return 1
    watcher = asyncio.create_task(supervisor.watch(asyncio.current_task()))
    try:
        # ── 3. baselines: killed mid-run, resumed ────────────────────────────────────────────
        killed = await asyncio.to_thread(kill_child_mid_run, url, approver, checkpoint, "baselines")
        steps["kill"] = killed
        print("KILL:", killed, flush=True)
        if not killed.get("killed"):
            findings.append("the kill did not land on an in-flight Baseline B run")
        report = await run_wave_here(url, approver, "baselines", checkpoint)
        steps["baselines_resume"] = {
            k: getattr(report, k)
            for k in ("started", "skipped_done", "reconciled", "interrupted", "reruns")
        }
        print("BASELINES RESUMED:", steps["baselines_resume"], flush=True)

        # ── 4. the refusal, before and after round 1 is generated ────────────────────────────
        async def refusal(wave: str) -> str | None:
            try:
                await run_wave_here(url, approver, wave, checkpoint)
            except GenerationStopped as e:
                return str(e)
            return None

        steps["refused_before_round1"] = await refusal("proposed:2")
        print("proposed:2 before round 1 exists ->", steps["refused_before_round1"], flush=True)
        await run_wave_here(url, approver, "proposed:1", checkpoint)
        steps["refused_while_round1_undecided"] = await refusal("proposed:2")
        print(
            "proposed:2 while round 1 is undecided ->",
            steps["refused_while_round1_undecided"],
            flush=True,
        )
        if not steps["refused_before_round1"] or not steps["refused_while_round1_undecided"]:
            findings.append("proposed:2 was NOT refused when it should have been")

        # ── 5. T6, then the stand-in approver ────────────────────────────────────────────────
        async def decide(runner, client, issuer):  # type: ignore[no-untyped-def]
            run = next(
                r
                for r in evidence(url)["runs"]
                if r["arm"] == "proposed" and r["status"] == "awaiting_approval"
            )
            seeded = next(iter(runner._seeded.values())) if runner._seeded else None
            if seeded is None:
                await runner.ensure_seeded("B01", "proposed")
                seeded = runner._seeded[("B01", "proposed")]
            body = {"reason": None, "failed_gate": None, "seconds_spent": 30}
            no = await client.post(
                f"/api/v1/runs/{run['run_id']}/approve",
                json=body,
                headers={"Authorization": f"Bearer {issuer.token(seeded.submitter_id)}"},
            )
            yes = await client.post(
                f"/api/v1/runs/{run['run_id']}/approve",
                json=body,
                headers={"Authorization": f"Bearer {runner.approver_token()}"},
            )
            return no.status_code, yes.status_code, run["run_id"]

        no_status, yes_status, decided_run = await with_runner(url, approver, decide)
        steps["approval"] = {"submitter_status": no_status, "stand_in_status": yes_status}
        print(
            "APPROVE by the submitter:",
            no_status,
            "| by the stand-in approver:",
            yes_status,
            flush=True,
        )
        if no_status != 403:
            findings.append(f"the submitter was NOT refused: {no_status}")
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            st = next(r["status"] for r in evidence(url)["runs"] if r["run_id"] == decided_run)
            if st not in ("queued", "awaiting_approval"):
                break
            await asyncio.sleep(3)

        # ── 6. round 2 after the decision ────────────────────────────────────────────────────
        await run_wave_here(url, approver, "proposed:2", checkpoint)
    finally:
        watcher.cancel()
        supervisor.stop()

    # ── 7. checks that cost nothing ──────────────────────────────────────────────────────────
    ev = evidence(url)
    steps["evidence"] = ev
    steps["worker_crashes"] = supervisor.crashes

    from scripts.phase7_stage2 import run_evidence

    p2 = [r for r in ev["runs"] if r["arm"] == "proposed" and r["brief"] and "ANC" in r["brief"]]
    steps["round2_run_evidence"] = run_evidence(url, UUID(p2[-1]["run_id"])) if p2 else None
    print("ROUND 2 read what round 1 wrote:", steps["round2_run_evidence"], flush=True)

    # The brief check is graph step 3. A run that read the FROZEN record makes no model call at or
    # before step 4. (The first version of this check compared `runs.brief_check`, which is written
    # only when a run ENDS, so it read null for every parked run and reported a false finding.)
    with psycopg.connect(url, autocommit=True) as c:
        early = c.execute("select count(*) from llm_calls where step <= 4").fetchone()
    steps["model_calls_at_or_before_step_4"] = int(early[0]) if early else None
    if steps["model_calls_at_or_before_step_4"]:
        findings.append("a run computed its own brief check instead of reading the frozen one")
    for r in ev["runs"]:
        if r["arm"] == "proposed" and r["status"] == "completed" and r["declared_gaps"]:
            findings.append(
                "a Proposed campaign completed with declared gaps "
                f"{[(g.get('agent'), g.get('infrastructure')) for g in r['declared_gaps']]}: "
                "it wrote no memory, so later rounds read nothing"
            )
    if not any(ev["memory_items_by_organisation"].values()):
        findings.append("round 2 read NO memory: the memory leg was not exercised")
    for name, k in ev["memory_items_by_organisation"].items():
        if "baseline" in name and k:
            findings.append(f"{name} holds {k} memory items: a baseline reached memory")
    if ev["refused_calls"]:
        findings.append(f"{ev['refused_calls']} calls were refused by the ledger")
    total = sum(n for _t, o, n in ev["llm_calls_by_tier_and_outcome"] if o != "refused")
    steps["calls_made"] = total
    print(
        f"CALLS MADE (non-refused): {total} of the dry run's ceiling {DRY_RUN_CEILING}", flush=True
    )
    steps["real_budget_file_unchanged"] = REAL_BUDGET.read_bytes() == real_budget_before
    if not steps["real_budget_file_unchanged"]:
        findings.append("config/evaluation_budget.yaml CHANGED")
    steps["findings"] = findings
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"dryrun_{stamp}.json"
    out.write_text(json.dumps(steps, indent=1, default=str) + "\n", encoding="utf-8")
    print("REPORT:", out)
    print("FINDINGS:", findings or "none from the checks above (read the report)", flush=True)
    return 0


def _db_exists() -> bool:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        return (
            c.execute("select 1 from pg_database where datname = %s", (THROWAWAY,)).fetchone()
            is not None
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--child", action="store_true")
    p.add_argument("--wave")
    p.add_argument("--url")
    p.add_argument("--approver")
    p.add_argument("--checkpoint")
    a = p.parse_args()
    if a.child:
        return child_main(a)
    return asyncio.run(main_async(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    sys.exit(main())
