"""Stage 2 of the Phase 7 freeze gate: does `R` separate campaigns 1, 2 and 3 on live memory?

Registered in `docs/phase7-first-decision.md`: "Registration 2" and "Stage-2 runner (3 November)".
For each brand it runs campaigns 1 and 2 LIVE on real models (research replayed from the evaluation
snapshot, each campaign approved through the product's own path, memory written back), then scores
campaign 3 from recall alone (no model call, as stage 1 did). THREE variants of `U` are computed
from the SAME recall of each campaign:

  old    U = 1 - (slots filled at M >= 0.80) / 2          bands D0 < 0.35 <= D1 < 0.70 <= D2
         (the original design; its bands are constants here, config no longer holds them)
  floor  U = 1 - min(1, n/12), n = recalled items with S >= 0.55   frozen bands 0.20 / 0.32
         (decision-log 2.21; config/scoring.yaml `necessity:`)
  count  U = 1 - min(1, n/12), n = EVERY recalled item     the same frozen bands

REGISTERED PREFERENCE ORDERING (owner, 3 November, before any run): old, then floor, then count; the
first that meets the criterion is adopted. This is what makes three variants one test.

Scope, in order (owner, 3 November): `interim` runs four brands, `rest` runs the others. `rest`
REFUSES unless the interim report exists and passed: 3 of 4 brands showing three distinct bands
(campaign 1 > 2 > 3) under at least one variant. Neither this file nor a flag can skip that.

Live calls are refused by the ledger until `billing_unlinked_confirmed_on` is set by the owner
(`config/dev_budget.yaml`); this runner never touches that file. It also refuses to start a live
stage unless the registered files are committed and unmodified, so the registration provably
precedes every call.

Usage (from backend/):
  PYTHONPATH=. uv run python -m scripts.phase7_stage2 --stage interim --dry-run   # no calls
  PYTHONPATH=. uv run python -m scripts.phase7_stage2 --stage interim              # live
  PYTHONPATH=. uv run python -m scripts.phase7_stage2 --stage rest
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
import yaml

from app.memory.scoring import (
    Necessity,
    ScoringConfig,
    load_scoring,
    necessity_depth,
    necessity_r,
    quantity_coverage,
)

Depth = Literal["D0", "D1", "D2"]
REPO = Path(__file__).resolve().parents[2]
TEST_SET = REPO / "evaluation" / "test_set.yaml"
OUT = Path("C:/BrandverseData/evaluation-runs")
INTERIM_REPORT = OUT / "stage2_interim.json"
REST_REPORT = OUT / "stage2_rest.json"
ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_stage2"
REDIS_URL = "redis://127.0.0.1:6379/14"
STORE = Path("C:/BrandverseData/evaluation-snapshot")

# The brands, in the order fixed here BEFORE any run: the eight clean non-regulated brands. B04 is
# contaminated (its stage-1 numbers were seen before the definition was chosen) and B07 is
# regulated (excluded from the criterion by construction); both are reported elsewhere. The interim
# set is the first four in this order, chosen by position and not by expected outcome.
CLEAN_BRANDS: tuple[str, ...] = ("B01", "B02", "B03", "B05", "B06", "B08", "B09", "B10")
INTERIM_BRANDS = CLEAN_BRANDS[:4]
REST_BRANDS = CLEAN_BRANDS[4:]
INTERIM_NEEDS = 3  # of 4, under either definition

# Stage 1's bands for the OLD definition of U. Superseded 1 November; kept only so the old
# definition is scored as it was registered, side by side.
OLD_D0_BELOW = 0.35
OLD_D2_FROM = 0.70

# Depth each live campaign is RUN at (the engine that would choose it is not built). Campaign 1 is a
# cold start (R = 0.786, D2 by construction). Campaign 2 is run at D1, the depth B04's campaign 1
# was run at and the one whose write-back volume (5 items) the predictions were reasoned from.
RUN_DEPTH: dict[int, Depth] = {1: "D2", 2: "D1"}

RANK = {"D0": 0, "D1": 1, "D2": 2}

# Registered predictions per definition, written before any stage-2 call. New: Registration 2
# section 3. Old: the definition's mechanism, stated in advance: items written by one campaign start
# at confidence 0.60 and so cannot reach M >= 0.80, which is why stage 1 saw no slot filled; the
# old U therefore stays 1 for campaigns 2 and 3 and the old definition is predicted NOT to separate
# them (C1 D2, C2 D1, C3 D1). Stage 1 on B04 informs this, so it is a prediction of a known failure
# mode, not a blind one.
PREDICTIONS: dict[str, dict[int, tuple[Depth, ...]]] = {
    "floor": {1: ("D2",), 2: ("D1", "D2"), 3: ("D0", "D1")},  # modal first
    # UNCERTAIN, and its basis is an artifact: stage 1 scored campaign 3 against campaign 1's
    # memory only, so "no item reaches M >= 0.80" was seen on memory that campaign 2 had not yet
    # added to. With campaigns 1 and 2 live, campaign 3 recalls more and older items; whether any
    # reaches 0.80 is not known. D1, D1 is the stage-1 expectation, held loosely.
    "old": {1: ("D2",), 2: ("D1",), 3: ("D1",)},
    # No floor: every recalled item counts, so n = items campaign 1 wrote (about 5 to 8) at
    # campaign 2 and campaign 1's plus campaign 2's (about 9 to 12, less merged duplicates) at
    # campaign 3. Alternatives: D2 at campaign 2 if campaign 1 wrote 4 or fewer items; D1 at
    # campaign 3 if the two campaigns wrote 8 or fewer between them.
    "count": {1: ("D2",), 2: ("D1", "D2"), 3: ("D0", "D1")},
}

# The registered preference ordering (owner, 3 November, before any run): the first variant that
# meets the criterion is the one adopted. The original design is preferred because changing a
# mechanism is justified only when the original fails. This ordering is what makes three variants
# one test and not three chances.
VARIANTS: tuple[str, ...] = ("old", "floor", "count")


@dataclass(frozen=True, slots=True)
class CampaignScore:
    request_id: str
    position: int
    novelty: float
    n_floor: int  # recalled items with S >= the floor (capped at 12)
    n_count: int  # recalled items, all of them (capped at 12)
    n_recalled: int
    slots_filled: int
    slots_total: int
    best_m: float
    u_old: float
    u_floor: float
    u_count: float
    r_old: float
    r_floor: float
    r_count: float
    depth_old: Depth
    depth_floor: Depth
    depth_count: Depth
    # sensitivity only, never to select: the floor variant's depth at the other floors
    depth_floor_at: dict[str, Depth] = field(default_factory=dict)

    def u(self, variant: str) -> float:
        return {"old": self.u_old, "floor": self.u_floor, "count": self.u_count}[variant]

    def r(self, variant: str) -> float:
        return {"old": self.r_old, "floor": self.r_floor, "count": self.r_count}[variant]

    def depth(self, variant: str) -> Depth:
        return {"old": self.depth_old, "floor": self.depth_floor, "count": self.depth_count}[
            variant
        ]


def old_depth(r: float) -> Depth:
    return "D0" if r < OLD_D0_BELOW else "D2" if r >= OLD_D2_FROM else "D1"


def score_campaign(
    *,
    request_id: str,
    position: int,
    novelty: float,
    similarities: Sequence[float],
    old_coverage: float,
    slots: tuple[int, int],
    best_m: float,
    regulated: bool,
    cfg: Necessity,
    floors: Sequence[float] = (0.50, 0.60),
) -> CampaignScore:
    """All three variants from one recall. Pure: no database, no model, no encoder.

    OLD: slots filled at M >= 0.80, stage-1 bands. FLOOR: n at S >= the frozen floor, frozen bands.
    COUNT: n = every recalled item (floor 0), the same frozen bands."""
    s_sector = 1.0 if regulated else 0.0
    n_floor, cov_floor = quantity_coverage(similarities, cfg)
    n_count, cov_count = quantity_coverage([1.0] * len(similarities), cfg)
    u_old, u_floor, u_count = 1.0 - old_coverage, 1.0 - cov_floor, 1.0 - cov_count
    r_old = necessity_r(novelty, u_old, s_sector, cfg)
    r_floor = necessity_r(novelty, u_floor, s_sector, cfg)
    r_count = necessity_r(novelty, u_count, s_sector, cfg)
    sens: dict[str, Depth] = {}
    for f in floors:
        _, c = quantity_coverage(similarities, cfg.model_copy(update={"relevance_floor": f}))
        sens[f"{f:.2f}"] = necessity_depth(necessity_r(novelty, 1.0 - c, s_sector, cfg), cfg)
    return CampaignScore(
        request_id=request_id,
        position=position,
        novelty=novelty,
        n_floor=n_floor,
        n_count=n_count,
        n_recalled=len(similarities),
        slots_filled=slots[0],
        slots_total=slots[1],
        best_m=best_m,
        u_old=u_old,
        u_floor=u_floor,
        u_count=u_count,
        r_old=r_old,
        r_floor=r_floor,
        r_count=r_count,
        depth_old=old_depth(r_old),
        depth_floor=necessity_depth(r_floor, cfg),
        depth_count=necessity_depth(r_count, cfg),
        depth_floor_at=sens,
    )


def separates(depths: Sequence[Depth]) -> bool:
    """Three different bands in the registered order: campaign 1 > campaign 2 > campaign 3."""
    return len(depths) == 3 and RANK[depths[0]] > RANK[depths[1]] > RANK[depths[2]]


def r_monotonic(rs: Sequence[float], margin: float = 0.05) -> bool:
    """The continuous version: R falls by at least `margin` from each campaign to the next."""
    return len(rs) == 3 and rs[0] - rs[1] >= margin and rs[1] - rs[2] >= margin


@dataclass(frozen=True, slots=True)
class InterimVerdict:
    passed: bool
    brands: int
    separating: dict[str, int]  # variant -> brands with three distinct bands
    adopted: str | None  # the FIRST variant, in the registered order, that meets the criterion
    needed: int


def interim_verdict(scores: dict[str, Sequence[CampaignScore]]) -> InterimVerdict:
    """3 of 4 brands with three distinct bands under a variant; the adopted variant is the first in
    the registered order (OLD, FLOOR, COUNT) that meets it, whatever the others show. Needs all
    four brands scored: a partial interim cannot pass."""
    full = {b: s for b, s in scores.items() if len(s) == 3}
    counts = {v: sum(separates([c.depth(v) for c in s]) for s in full.values()) for v in VARIANTS}
    complete = len(full) == len(INTERIM_BRANDS) and set(full) == set(INTERIM_BRANDS)
    adopted = next((v for v in VARIANTS if complete and counts[v] >= INTERIM_NEEDS), None)
    return InterimVerdict(adopted is not None, len(full), counts, adopted, INTERIM_NEEDS)


def brand_requests(path: Path = TEST_SET) -> dict[str, tuple[str, str, str]]:
    """{"B01": ("R01", "R02", "R03"), ...} in campaign order (position 1, 2, 3)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, dict[int, str]] = {}
    for r in data["requests"]:
        out.setdefault(r["brand"], {})[int(r["position"])] = r["id"]
    return {b: (p[1], p[2], p[3]) for b, p in out.items()}


def recorded_requests(store: Path = STORE) -> set[str]:
    """Requests whose research is committed to the snapshot (so a replayed run can answer)."""
    done: set[str] = set()
    for line in (store / "committed.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(str(json.loads(line)["request"]))
    return done


def runnable(brands: Sequence[str], recorded: set[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Brands whose campaigns 1 and 2 can both be replayed, and what the others lack."""
    reqs = brand_requests()
    ok: list[str] = []
    missing: dict[str, list[str]] = {}
    for b in brands:
        lack = [r for r in reqs[b][:2] if r not in recorded]
        if lack:
            missing[b] = lack
        else:
            ok.append(b)
    return ok, missing


REGISTERED_FILES = (
    "config/scoring.yaml",
    "config/dev_budget.yaml",
    "docs/phase7-first-decision.md",
    "docs/decision-log.md",
    "backend/tests/test_necessity_frozen.py",
    "backend/scripts/phase7_stage2.py",
)


def registration_problem() -> str | None:
    """None when every registered file is committed and unmodified, else why not. A live stage
    refuses otherwise: the registration must precede the first call, provably."""
    status = subprocess.run(  # noqa: S603 - fixed command
        ["git", "status", "--porcelain", "--", *REGISTERED_FILES],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if status:
        return f"registered files are not committed:\n{status}"
    return None


def head_commit() -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def rest_refusal(report_path: Path = INTERIM_REPORT) -> str | None:
    """Why `rest` may not run: the interim report must exist and say it passed."""
    if not report_path.exists():
        return f"no interim report at {report_path}: run --stage interim first"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("verdict", {}).get("passed"):
        return f"interim criterion not met ({report.get('verdict')}): the rest do not run"
    return None


# ── The live part ────────────────────────────────────────────────────────────────────────────


async def recall_and_score(
    url: str,
    enc: Any,
    cfg: ScoringConfig,
    *,
    org: UUID,
    brand_id: UUID,
    product_id: UUID | None,
    rid: str,
    position: int,
    prior_vectors: list[list[float]],
    inputs: Any,
) -> tuple[CampaignScore, list[float]]:
    """Step-4 recall for one request against the memory that exists NOW, scored both ways. No model
    call. Returns the score and the request's own vector (for later novelty)."""
    from app.memory.recall import recall

    text = f"{inputs.category}. {inputs.sector}. {inputs.brief} {inputs.audience}"
    vec = enc.embed_documents([text])[0]
    novelty = (
        1.0
        if not prior_vectors
        else 1.0
        - max(float(sum(a * b for a, b in zip(vec, p, strict=True))) for p in prior_vectors)
    )
    if position == 1:
        sims: list[float] = []
        old_cov, slots, best = 0.0, (0, 2), 0.0
    else:
        async with await psycopg.AsyncConnection.connect(url) as conn:
            found = await recall(
                conn,
                enc,
                cfg,
                org_id=org,
                query_text=f"{inputs.category} {inputs.sector.replace('_', ' ')} {inputs.brief}",
                product_id=None if product_id is None else str(product_id),
                brand_id=str(brand_id),
                product_category=inputs.category,
                sector=inputs.sector,
                now=datetime.now(UTC),
                personas=None,
            )
        sims = [i.components["S"] for i in found.items if "S" in i.components]
        old_cov = found.coverage
        slots = (found.slots_filled, found.slots_total)
        best = max((i.m for i in found.items), default=0.0)
    score = score_campaign(
        request_id=rid,
        position=position,
        novelty=novelty,
        similarities=sims,
        old_coverage=old_cov,
        slots=slots,
        best_m=best,
        regulated=False,
        cfg=cfg.necessity,
    )
    return score, vec


def print_table(brand: str, scores: Sequence[CampaignScore]) -> None:
    print(
        f"\n{brand}: campaign | N | n floor / count of recalled | slots | "
        "U old/floor/count | R old/floor/count | depth old/floor/count | floor@.50/.60"
    )
    for c in scores:
        sens = "/".join(c.depth_floor_at.values()) or "-"
        print(
            f"  C{c.position} {c.request_id} | {c.novelty:.3f} "
            f"| {c.n_floor:2d} / {c.n_count:2d} of {c.n_recalled:2d} "
            f"| {c.slots_filled}/{c.slots_total} "
            f"| {c.u_old:.2f}/{c.u_floor:.2f}/{c.u_count:.2f} "
            f"| {c.r_old:.3f}/{c.r_floor:.3f}/{c.r_count:.3f} "
            f"| {c.depth_old}/{c.depth_floor}/{c.depth_count} | {sens}"
        )
    for v in VARIANTS:
        hits = [c.position for c in scores if c.depth(v) in PREDICTIONS[v][c.position]]
        print(
            f"  {v}: separates {separates([c.depth(v) for c in scores])}; "
            f"R falls by 0.05 each step {r_monotonic([c.r(v) for c in scores])}; "
            f"prediction hit for campaigns {hits}"
        )


ATTEMPT_STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
CRASH_LOG = Path("C:/BrandverseData/logs/stage2-crashes.jsonl")
MAX_WORKER_RESTARTS = 12
# What the runner is doing right now, for the crash log (updated by `run_brand` before each POST).
CURRENT_CAMPAIGN: dict[str, Any] = {}


def last_step_in(log_text: str) -> dict[str, Any] | None:
    """The last pipeline step line a worker log holds ("step 6 research: start" and the run it was
    for), read from the end. The log is unbuffered, so this is where the worker had got to."""
    for line in reversed(log_text.splitlines()):
        if '"msg": "step ' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = str(entry.get("msg", ""))
        if msg.startswith("step "):
            return {"line": msg, "run_id": entry.get("run_id"), "ts": entry.get("ts")}
    return None


class WorkerSupervisor:
    """Starts the stage-2 worker and starts it AGAIN when it dies.

    A STOPGAP FOR THE INTERIM ONLY (owner, 2026-11-04), never the evaluation's fix. The worker has
    died of access violations in native code at about 1 research execution in 5 (decision-log
    2.25), silently: the run stays `queued` and nothing parks. On a death this records it (time,
    exit code, the campaign being run, the step the worker had reached, the incarnation), starts a
    new worker, and lets the worker's own start-up recovery sweep resume the run from its
    checkpoint (at-least-once: the step that was running runs again). The crash rate is MEASURED
    from `crashes` and the crash log, not assumed. The interim reports no latency anyone will use,
    so the minutes a restart costs do not matter to it. After `max_restarts` it gives up."""

    def __init__(
        self,
        launch: Callable[[int], tuple[Any, Path]],
        *,
        crash_log: Path = CRASH_LOG,
        max_restarts: int = MAX_WORKER_RESTARTS,
        ready_timeout_s: float = 180.0,
        poll_s: float = 2.0,
        campaign: dict[str, Any] | None = None,
    ) -> None:
        self._launch, self._crash_log = launch, crash_log
        self._max, self._ready_s, self._poll_s = max_restarts, ready_timeout_s, poll_s
        self._campaign = campaign if campaign is not None else CURRENT_CAMPAIGN
        self.proc: Any = None
        self.log_path: Path | None = None
        self.incarnation = 0
        self.crashes: list[dict[str, Any]] = []
        self.logs: list[Path] = []
        self.gave_up: str | None = None
        self._stopping = False

    async def _start_one(self) -> bool:
        self.proc, self.log_path = self._launch(self.incarnation)
        self.logs.append(self.log_path)
        waited = 0.0
        while waited < self._ready_s:
            await asyncio.sleep(self._poll_s)
            waited += self._poll_s
            if "pipeline ready" in self.log_path.read_text(encoding="utf-8", errors="replace"):
                return True
            if self.proc.poll() is not None:
                return False
        return False

    async def start(self) -> bool:
        return await self._start_one()

    def _record_crash(self) -> dict[str, Any]:
        if self.log_path is None:
            raise RuntimeError("a crash was recorded before any worker was launched")
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        entry = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "incarnation": self.incarnation,
            "exit_code": self.proc.returncode,
            "campaign": dict(self._campaign),
            "last_step": last_step_in(text),
            "worker_log": str(self.log_path),
            "note": "0xC0000005 (3221225477 / -1073741819) is an access violation; the Windows "
            "Application log (Event 1000, python.exe) names the module",
        }
        self.crashes.append(entry)
        self._crash_log.parent.mkdir(parents=True, exist_ok=True)
        with self._crash_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    async def watch(self, main: "asyncio.Task[Any] | None") -> None:
        """Restart the worker whenever it dies; cancel `main` if it cannot be brought back."""
        while not self._stopping:
            if self.proc.poll() is None:
                await asyncio.sleep(self._poll_s)
                continue
            if self._stopping:
                return
            entry = self._record_crash()
            print(
                f"WORKER DIED (crash {len(self.crashes)}) exit {entry['exit_code']} while "
                f"{entry['campaign']} at {entry['last_step']}",
                flush=True,
            )
            if len(self.crashes) > self._max:
                self.gave_up = f"{len(self.crashes)} worker deaths: over the {self._max} allowed"
            else:
                self.incarnation += 1
                if await self._start_one():
                    print(f"worker restarted (incarnation {self.incarnation})", flush=True)
                    continue
                self.gave_up = f"the worker would not start again (incarnation {self.incarnation})"
            print("GIVING UP:", self.gave_up, flush=True)
            if main is not None:
                main.cancel()
            return

    def stop(self) -> None:
        self._stopping = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=30)

    def research_starts(self) -> int:
        """Research steps begun across every incarnation: the denominator of the crash rate."""
        return sum(
            p.read_text(encoding="utf-8", errors="replace").count('"msg": "step 6 research: start')
            for p in self.logs
            if p.exists()
        )


async def run_live(brands: Sequence[str], stage: str, probe: bool = False) -> int:
    """Campaigns 1 and 2 live per brand, campaign 3 from recall; resumable per brand.

    `probe`: ONE brand (the first), campaign 1 only, then stop. It writes NO report (a report would
    read as an interim result and wake the recording queue) and is for finding out whether a live
    run completes at all (stage-2 interim, 2026-11-04: step 6 never finished)."""
    if probe:
        brands = list(brands)[:1]
    # Imported here so the pure functions above (and their tests) load without the app, the encoder
    # or a database.
    import httpx
    from pydantic import SecretStr

    from app.api.main import create_app
    from app.core.config import get_settings
    from app.evaluation.test_set import request_inputs
    from app.memory.embeddings import default_encoder
    from scripts.chunk_b_d1_replay import seed
    from scripts.chunk_f_first_campaign import wait_for_campaign, wait_for_end
    from scripts.e2e_prepare_db import main as prepare_database
    from tests.auth_keys import TestIssuer
    from tests.db.target_guard import managed_supabase_hosts

    base = get_settings()
    if base.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    problem = registration_problem()
    if problem:
        print("REFUSED:", problem)
        return 1
    ok, missing = runnable(brands, recorded_requests())
    if missing:
        print("These brands cannot be replayed (research not recorded):", missing)
    if not ok:
        return 1
    url = prepare_database(ADMIN_URL, DB_NAME) if not _db_exists() else _db_url()
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    # A run left in progress by a crashed attempt would be RESUMED by the worker's recovery sweep at
    # start-up, in parallel with the new run, doubling the CPU load and the job time (found
    # 2026-11-04: the probe's step 3 took 277 s instead of 8 s and ARQ killed its job at 600 s).
    # This database is the stage-2 scratch database only; its stale runs are closed, and counted.
    with psycopg.connect(url, autocommit=True) as c:
        closed = c.execute(
            "update runs set status = 'failed', completed_at = now() "
            "where status in ('queued', 'awaiting_approval')"
        ).rowcount
    print(f"closed {closed} stale in-progress run(s) left by an earlier attempt", flush=True)
    remaining = calls_remaining(url)
    print(f"development calls remaining under the ceiling: {remaining}", flush=True)
    if (refusal := budget_floor_refusal(remaining)) is not None:
        print("REFUSED:", refusal)
        return 1
    inputs_all = request_inputs()
    reqs = brand_requests()
    cfg = load_scoring()
    enc = default_encoder()
    settings = base.model_copy(
        update={
            "database_url": SecretStr(url),
            "redis_url": REDIS_URL,
            "environment": "development",
            "pipeline_agents": "real",
            "research_snapshot_dir": STORE,
            "max_llm_calls_per_run": 10,
            "llm_allow_strong_tier": False,
            "llm_strong_tier_org_id": None,
            "run_events_block_ms": 200,
        }
    )
    issuer = TestIssuer()
    app = create_app(settings, verifier=issuer.verifier())

    def launch(incarnation: int) -> tuple[Any, Path]:
        # One log file per incarnation, so a restart does not overwrite what the dead one wrote.
        suffix = "" if incarnation == 0 else f".{incarnation}"
        # One file per ATTEMPT as well as per incarnation: a retried interim used to overwrite the
        # worker log of the attempt before it, which lost the evidence of why it failed.
        log_path = Path(tempfile.gettempdir()) / f"stage2_worker_{ATTEMPT_STAMP}{suffix}.log"
        proc = subprocess.Popen(  # noqa: S603, ASYNC220
            [sys.executable, "-m", "app.workers"],
            env=worker_env,
            stdout=log_path.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
        return proc, log_path

    worker_env = {
        **os.environ,
        "DATABASE_URL": url,
        "REDIS_URL": REDIS_URL,
        "ENVIRONMENT": "development",
        "PIPELINE_AGENTS": "real",
        "RESEARCH_SNAPSHOT_DIR": str(STORE),
        "MAX_LLM_CALLS_PER_RUN": "10",
        "LLM_ALLOW_STRONG_TIER": "false",
        "LLM_STRONG_TIER_ORG_ID": "",
        # A named timeout on the research step, instead of a blanket 15-minute wait. There is NO
        # repeating stack dump: `faulthandler.dump_traceback_later` crashed the worker mid-dump
        # (2026-11-04, 2.25), and the worker's own `faulthandler.enable()` still prints a stack at
        # a fatal signal. The worker's log goes to a file: Python block-buffers a redirected
        # stdout, so lines written before a termination were lost.
        "PYTHONUNBUFFERED": "1",
        # ARQ kills a run job at this limit. 600 s (the default) is too tight for a D2 run on
        # this laptop: research alone is 205 s to 313 s, and it doubles under contention.
        "WORKER_JOB_TIMEOUT_S": "1800",
        # 900 s: probe 2 measured research at 358.6 s alone on AC, so 420 s left no room.
        "RESEARCH_STEP_TIMEOUT_S": "900",
        # Thread caps (OMP_NUM_THREADS, MKL_NUM_THREADS, WORKER_TORCH_THREADS) are DISABLED
        # here (owner, 2026-11-04): probe 2 completed uncapped, so a cap addresses an
        # unestablished cause and would make a clean pass uninterpretable. The setting stays
        # in config, off.
    }
    results: dict[str, list[CampaignScore]] = {}
    supervisor = WorkerSupervisor(launch)
    try:
        if not await supervisor.start():
            print("worker did not become ready; log:", supervisor.log_path)
            return 1
        # A worker that dies leaves its run `queued` and the runner waiting for a campaign that
        # will never park (the first crash cost 15 minutes of that; 4 November 2026). The
        # supervisor notices at once, records the death, starts a new worker, and the new
        # worker's recovery sweep resumes the run. A STOPGAP for the interim only (2.25).
        watcher = asyncio.create_task(supervisor.watch(asyncio.current_task()))
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test", timeout=60
                ) as client:
                    for b in ok:
                        r1, r2, r3 = reqs[b]
                        results[b] = await run_brand(
                            url,
                            client,
                            issuer,
                            enc,
                            cfg,
                            inputs_all,
                            (r1, r2, r3),
                            seed,
                            wait_for_campaign,
                            wait_for_end,
                            stop_after=1 if probe else 3,
                        )
                        if not probe:
                            print_table(b, results[b])
        except asyncio.CancelledError:
            if supervisor.gave_up is None:
                raise  # cancelled from outside, not by the supervisor
            print(f"STOPPED: {supervisor.gave_up}; no result.", flush=True)
            return 2
        except InvalidCampaign as e:
            print(
                f"INVALID RUN, NO REPORT WRITTEN: {e}. TALLY (this database): {tally(url)}",
                flush=True,
            )
            return 3
        finally:
            watcher.cancel()
    finally:
        supervisor.stop()
    rate = {
        "worker_crashes": len(supervisor.crashes),
        "research_steps_started": supervisor.research_starts(),
        "crash_log": str(CRASH_LOG),
        "note": "STOPGAP supervision for the interim only (decision-log 2.25); the rate is "
        "crashes / research steps started, counted from the worker logs",
    }
    print("WORKER CRASH RATE:", rate, flush=True)
    if probe:
        print("PROBE COMPLETE (no report written). TALLY (this database):", tally(url))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - one local directory, at the end
    verdict = interim_verdict(results) if stage == "interim" else None
    report = {
        "stage": stage,
        "at": datetime.now(UTC).isoformat(),
        "registration_commit": head_commit(),
        "brands": {b: [asdict(c) for c in s] for b, s in results.items()},
        "verdict": None if verdict is None else asdict(verdict),
        "llm_calls": tally(url),
        "worker_supervision": {**rate, "crashes": supervisor.crashes},
    }
    (INTERIM_REPORT if stage == "interim" else REST_REPORT).write_text(
        json.dumps(report, indent=1) + "\n", encoding="utf-8"
    )
    print("\nTALLY (this database):", report["llm_calls"])
    if verdict is not None:
        print("INTERIM VERDICT:", verdict)
    return 0


def _db_exists() -> bool:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        return (
            c.execute("select 1 from pg_database where datname = %s", (DB_NAME,)).fetchone()
            is not None
        )


def _db_url() -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME


BUDGET_FLOOR = 30


def budget_floor_refusal(remaining: int, floor: int = BUDGET_FLOOR) -> str | None:
    """Owner ruling, 5 November 2026: the development ceiling holds at 250 and is not raised
    quietly. When fewer than `floor` calls remain, no attempt starts; the owner decides."""
    if remaining < floor:
        return (
            f"only {remaining} of the development ceiling's calls remain (floor {floor}): "
            "come back to the owner; the ceiling is not raised quietly"
        )
    return None


def calls_remaining(url: str) -> int:
    """Calls left under the ceiling: the ceiling minus the owner's figure at the ruling plus what
    this database has logged since (the ledger's own sum, config/dev_budget.yaml)."""
    from app.llm.dev_budget import load_dev_budget

    budget = load_dev_budget()
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute(
            "select count(*) filter (where outcome <> 'refused') from llm_calls"
        ).fetchone()
    made_here = int(row[0]) if row else 0
    return budget.ceiling_calls - budget.total(DB_NAME, made_here)


def tally(url: str) -> dict[str, int]:
    with psycopg.connect(url, autocommit=True) as c:
        rows = c.execute(
            "select tier, count(*) from llm_calls where outcome <> 'refused' group by tier"
        ).fetchall()
    return {str(t): int(n) for t, n in rows}


async def run_brand(
    url: str,
    client: Any,
    issuer: Any,
    enc: Any,
    cfg: ScoringConfig,
    inputs_all: dict[str, Any],
    request_ids: tuple[str, str, str],
    seed_fn: Any,
    wait_for_campaign: Any,
    wait_for_end: Any,
    stop_after: int = 3,
) -> list[CampaignScore]:
    """One brand: its own organisation (so no memory crosses brands), campaign 1 live, campaign 2
    live, campaign 3 scored from recall. Each campaign is SCORED before it runs, from the memory
    that exists then."""
    r1, r2, r3 = request_ids
    i1, i2, i3 = (inputs_all[r] for r in (r1, r2, r3))
    org, product_a, _run, user = seed_fn(url, _as_dict(i1))
    with psycopg.connect(url, autocommit=True) as c:
        for role in ("editor", "approver"):
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, user, role),
            )
        brand_id = c.execute(
            "select brand_id from products where id = %s", (product_a,)
        ).fetchone()[0]  # type: ignore[index]
        product_b = _add_product(c, org, brand_id, user, i2)
    headers = {"Authorization": f"Bearer {issuer.token(user)}"}
    scores: list[CampaignScore] = []
    vectors: list[list[float]] = []
    plan = ((1, r1, i1, product_a), (2, r2, i2, product_b), (3, r3, i3, product_a))
    for pos, rid, inp, product in plan:
        CURRENT_CAMPAIGN.update(request=rid, campaign=pos, brand_id=str(brand_id))
        score, vec = await recall_and_score(
            url,
            enc,
            cfg,
            org=org,
            brand_id=brand_id,
            product_id=product if pos > 1 else None,
            rid=rid,
            position=pos,
            prior_vectors=list(vectors),
            inputs=inp,
        )
        scores.append(score)
        vectors.append(vec)
        if pos == 3:
            break
        with psycopg.connect(url, autocommit=True) as c:
            before = c.execute(
                "select count(*) from memory_items where brand_id = %s", (brand_id,)
            ).fetchone()
        items_before = int(before[0]) if before else 0
        resp = await client.post(
            "/api/v1/runs",
            json={
                "org_id": str(org),
                "product_id": str(product),
                "brief": inp.brief,
                "audience": inp.audience,
                "depth": RUN_DEPTH[pos],
            },
            headers=headers,
        )
        if resp.status_code != 202:
            raise RuntimeError(f"{rid}: POST /runs -> {resp.status_code} {resp.text}")
        run_id = UUID(resp.json()["run_id"])
        view = await wait_parked_or_ended(url, client, run_id, headers)
        if view is None:
            raise RuntimeError(f"{rid}: the run never parked for approval")
        ap = await client.post(
            f"/api/v1/runs/{run_id}/approve",
            json={"reason": None, "failed_gate": None, "seconds_spent": 30},
            headers=headers,
        )
        if ap.status_code >= 300:
            raise RuntimeError(f"{rid}: approve -> {ap.status_code} {ap.text}")
        await wait_for_end(url, run_id)
        with psycopg.connect(url, autocommit=True) as c:
            wrote = c.execute(
                "select count(*) from memory_items where brand_id = %s", (brand_id,)
            ).fetchone()
            ended = c.execute(
                "select status, declared_gaps from runs where id = %s", (run_id,)
            ).fetchone()
        print(
            f"{rid}: campaign {pos} approved; "
            f"memory items for the brand now {wrote[0] if wrote else '?'}",
            flush=True,
        )
        problem = campaign_problem(
            ended[0] if ended else None,
            list(ended[1]) if ended else [],
            items_before,
            int(wrote[0]) if wrote else 0,
        )
        if problem is not None:
            raise InvalidCampaign(f"{rid} (campaign {pos}): {problem}")
        if pos >= stop_after:
            break
    return scores


async def wait_parked_or_ended(
    url: str, client: Any, run_id: UUID, headers: dict[str, str], timeout_s: float = 900.0
) -> dict[str, Any] | None:
    """Wait for the campaign to park for approval, OR notice that the run ENDED without parking.

    The shared waiter polls the campaign endpoint for 15 minutes, which is what a run that the
    circuit breaker aborted, or that was invalidated by an outage gap (both end `failed` and
    never park), would cost every time: 15 minutes of waiting for something that cannot happen.
    This one also reads the run's status and stops at once, with the recorded reason."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        response = await client.get(f"/api/v1/runs/{run_id}/campaign", headers=headers)
        if response.status_code == 200:
            body: dict[str, Any] = response.json()
            return body
        with psycopg.connect(url, autocommit=True) as c:
            row = c.execute("select status, failure from runs where id = %s", (run_id,)).fetchone()
        if row is not None and row[0] in ("failed", "blocked", "partial", "completed"):
            failure = row[1] or {}
            raise InvalidCampaign(
                f"the run ended {row[0]!r} without parking: "
                f"{failure.get('kind', 'no reason recorded')}: {failure.get('message', '')}"
            )
        await asyncio.sleep(3.0)
    return None


class InvalidCampaign(RuntimeError):
    """A live campaign did not produce what the measurement needs, so nothing scored from it, or
    after it, can mean anything. Stops the run; no report is written."""


def campaign_problem(
    status: str | None, gaps: Sequence[dict[str, Any]], items_before: int, items_after: int
) -> str | None:
    """Why a live campaign cannot be scored, or None. Found 5 November 2026: the provider answered
    503 to every agent call, the runs still ended `completed` (with declared gaps, no copy and no
    personas), no memory was written, every later campaign recalled nothing, and the interim
    reported 'failed' on empty inputs after 30 minutes and 53 calls. A campaign that did not write
    memory is not an observation of how memory changes R."""
    if status != "completed":
        return f"the run ended {status!r}, not 'completed'"
    failed = sorted({str(g["agent"]) for g in gaps if g.get("agent")})
    if failed:
        errors = sorted({str(g.get("error")) for g in gaps if g.get("agent")})
        return (
            f"agents failed ({', '.join(failed)}; {', '.join(errors)}): the campaign has no "
            "complete copy and writes no memory"
        )
    if items_after <= items_before:
        return f"it added no memory ({items_before} items before, {items_after} after)"
    return None


def _as_dict(i: Any) -> dict[str, object]:
    return {
        "brand": i.brand,
        "sector": i.sector,
        "product": i.product,
        "category": i.category,
        "claims": list(i.claims),
        "evidence_url": i.evidence_url,
        "brief": i.brief,
        "audience": i.audience,
        "domains": list(i.domains),
    }


def _add_product(c: Any, org: UUID, brand_id: UUID, user: UUID, inp: Any) -> UUID:
    import json as _json

    row = c.execute(
        "insert into products (org_id, brand_id, name, category) "
        "values (%s, %s, %s, %s) returning id",
        (org, brand_id, inp.product, inp.category),
    ).fetchone()
    pid: UUID = row[0]
    with c.transaction():
        c.execute(
            "select set_config('role', 'authenticated', true), "
            "set_config('request.jwt.claims', %s, true)",
            (_json.dumps({"sub": str(user), "role": "authenticated"}),),
        )
        for claim in inp.claims:
            c.execute(
                "insert into product_claims (org_id, product_id, claim, claim_type, "
                "evidence_url, created_by)"
                " values (%s, %s, %s, 'verified', %s, 'human')",
                (org, pid, claim, inp.evidence_url),
            )
    return pid


def dry_run(stage: str) -> int:
    brands = INTERIM_BRANDS if stage == "interim" else REST_BRANDS
    ok, missing = runnable(brands, recorded_requests())
    reqs = brand_requests()
    print(f"stage {stage}: brands {list(brands)}")
    for b in brands:
        print(
            f"  {b}: campaigns {reqs[b]}  live {reqs[b][:2]}  "
            f"{'replayable' if b in ok else 'NOT replayable, missing ' + str(missing[b])}"
        )
    n = len(ok)
    print(
        f"planned live campaigns: {2 * n}; about {7 * 2 * n} Standard calls "
        "(7 per campaign, repairs extra)"
    )
    print("registration:", registration_problem() or "committed and unmodified")
    print("rest refusal:", rest_refusal() if stage == "rest" else "n/a")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("interim", "rest"), required=True)
    p.add_argument(
        "--dry-run", action="store_true", help="plan and preflight only; no database, no calls"
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="one brand, campaign 1 only, no report: does a live run complete at all?",
    )
    a = p.parse_args()
    if a.probe and a.stage != "interim":
        p.error("--probe is for the interim stage only")
    if a.dry_run:
        return dry_run(a.stage)
    if a.stage == "rest":
        why = rest_refusal()
        if why:
            print("REFUSED:", why)
            return 1
    brands = INTERIM_BRANDS if a.stage == "interim" else REST_BRANDS
    return asyncio.run(
        run_live(brands, a.stage, probe=a.probe), loop_factory=asyncio.SelectorEventLoop
    )


if __name__ == "__main__":
    sys.exit(main())
