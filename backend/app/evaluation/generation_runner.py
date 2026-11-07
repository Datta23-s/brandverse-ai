"""The generation run's real I/O: the API and the database behind `generation.Runner` (2.50).

`generation.py` is the driver (checkpoint, resume, re-run cap, waves); this is the half that talks
to the system: it seeds the evaluation's organisations, starts runs through the product's own
endpoint, waits for them, and reads the human decisions. It never decides anything itself.

* **One organisation per (brand, arm).** Memory is per organisation and written on approval, so the
  Baselines' organisations cannot reach the Proposed arm's memory by construction, on top of the
  graph's own refusal (decision-log 2.48). A brand's three Proposed campaigns share one
  organisation, which is what makes campaign 2 read what campaign 1's approval wrote.
* **The harness cannot approve.** It submits as an `owner` and `editor`; the APPROVER is a different
  user holding only the approver role (Rules.md T6: owner and editor do not confer approval). This
  class has no method that approves or rejects; `decided` only READS `approvals`.
* **A run is found by what it is**, never by a remembered id: its organisation (brand and arm), its
  product, its arm and its brief. That is how a run started by a harness that was then killed is
  found again (`find_orphan`).
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg

from app.evaluation.generation import GenerationStopped, Item, Outcome
from app.evaluation.test_set import RequestInputs, RequestMeta, request_index, request_inputs

# The depth each arm is ASKED for. The graph decides the real one (Baseline B is D2 whatever is
# asked, Baseline A is D0, the Proposed arm's depth comes from the necessity engine): this is only
# what is recorded as requested.
ASKED_DEPTH = {"proposed": "D2", "baseline_b": "D2", "baseline_a": "D0"}
SUBMITTER_ROLES = ("owner", "editor")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# The databases that hold evaluation data. A harness aimed at one of them by a typo is the failure
# the dry run exists to prevent, so the dry run's target check names them.
EVALUATION_DATABASES = frozenset({"brandverse_evaluation"})
SKEW = timedelta(seconds=5)  # database and harness clocks are one machine's; this is margin only


def classify(status: str, failure: Mapping[str, Any] | None) -> tuple[Outcome, str | None]:
    """A run's `status` and `failure`, as the driver's outcomes.

    `failed` with an infrastructure kind (a provider outage, a job timeout, a crash, and the
    invalidation of a run whose agent gap was an outage) is `invalidated`: nobody read its copy and
    it is re-run, capped. `failed` without one is a RESULT (`ended`), as are `completed`, `partial`
    and `blocked`. An unknown status raises: a status the driver does not understand must not be
    treated as either."""
    if status == "awaiting_approval":
        return "parked", None
    if status == "queued":
        return "running", None
    if status == "failed":
        kind = (failure or {}).get("infrastructure_failure")
        return ("invalidated", str(kind)) if kind else ("ended", None)
    if status in ("completed", "partial", "blocked"):
        return "ended", None
    raise ValueError(f"unknown run status {status!r}")


def describe_target(url: str) -> tuple[str, int, str]:
    """(host, port, database) of a connection URL."""
    p = urlparse(url)
    return p.hostname or "", p.port or 5432, (p.path or "/").lstrip("/")


def target_refusal(url: str, expected_database: str) -> str | None:
    """Why the harness may not run against `url`, or None. Checked BEFORE the first call and printed
    by the caller: a loopback host, the one database named, and never an evaluation database."""
    host, _port, database = describe_target(url)
    if host not in LOOPBACK:
        return f"host {host!r} is not loopback: the dry run touches only the local database"
    if database in EVALUATION_DATABASES:
        return f"{database!r} is an evaluation database: this build runs the throwaway only"
    if database != expected_database:
        return f"database is {database!r}, expected {expected_database!r}"
    return None


@dataclass(frozen=True, slots=True)
class SeededOrg:
    org_id: UUID
    submitter_id: UUID
    brand_id: UUID
    products: Mapping[str, UUID]  # "A" / "B" -> product id


class ApiRunner:
    """`generation.Runner` over the API (an `httpx` client) and the database (a URL)."""

    def __init__(
        self,
        *,
        client: Any,
        dsn: str,
        token_for: Callable[[UUID], str],
        approver_id: UUID,
        scope: Sequence[str] | None = None,
        poll_s: float = 3.0,
        wait_timeout_s: float = 2400.0,
        inputs: Mapping[str, RequestInputs] | None = None,
        index: Mapping[str, RequestMeta] | None = None,
    ) -> None:
        self._client, self._dsn, self._token_for = client, dsn, token_for
        self._approver_id = approver_id
        self._poll_s, self._wait_timeout_s = poll_s, wait_timeout_s
        self._inputs = dict(inputs) if inputs is not None else request_inputs()
        self._index = dict(index) if index is not None else request_index()
        self._scope = list(scope) if scope is not None else list(self._index)
        self._seeded: dict[tuple[str, str], SeededOrg] = {}
        self._by_run_id: dict[str, tuple[str, str]] = {}

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def org_name(brand_id: str, arm: str) -> str:
        return f"EVAL {brand_id} {arm}"

    async def ensure_seeded(self, brand_id: str, arm: str) -> SeededOrg:
        """The organisation for (brand, arm): found by name if it exists (a resumed harness), else
        created with its brand, its products and their verified claims."""
        key = (brand_id, arm)
        if key in self._seeded:
            return self._seeded[key]
        name = self.org_name(brand_id, arm)
        requests = [r for r in self._index if self._index[r].brand_id == brand_id]
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as c:
            cur = await c.execute("select id from organisations where name = %s", (name,))
            row = await cur.fetchone()
            if row is None:
                seeded = await self._create(c, name, brand_id, arm, requests)
            else:
                seeded = await self._load(c, row[0], requests)
        self._seeded[key] = seeded
        return seeded

    async def _create(
        self, c: Any, name: str, brand_id: str, arm: str, requests: Sequence[str]
    ) -> SeededOrg:
        first = self._inputs[requests[0]]
        submitter = uuid4()
        await c.execute("insert into auth.users (id) values (%s)", (submitter,))
        await c.execute(
            "insert into auth.users (id) values (%s) on conflict do nothing", (self._approver_id,)
        )
        cur = await c.execute("insert into organisations (name) values (%s) returning id", (name,))
        org: UUID = (await cur.fetchone())[0]
        for role in SUBMITTER_ROLES:
            await c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, submitter, role),
            )
        await c.execute(
            "insert into org_members (org_id, user_id, role) values (%s, %s, 'approver')",
            (org, self._approver_id),
        )
        cur = await c.execute(
            "insert into brands (org_id, name, sector, domains) values (%s, %s, %s, %s) "
            "returning id",
            (org, first.brand, first.sector, list(first.domains)),
        )
        brand: UUID = (await cur.fetchone())[0]
        products: dict[str, UUID] = {}
        for rid in requests:
            key = self._index[rid].product_key
            if key in products:
                continue
            inp = self._inputs[rid]
            cur = await c.execute(
                "insert into products (org_id, brand_id, name, category) "
                "values (%s, %s, %s, %s) returning id",
                (org, brand, inp.product, inp.category),
            )
            products[key] = (await cur.fetchone())[0]
            await self._add_claims(c, org, products[key], submitter, inp)
        return SeededOrg(org, submitter, brand, products)

    @staticmethod
    async def _add_claims(c: Any, org: UUID, product: UUID, user: UUID, inp: RequestInputs) -> None:
        import json

        async with c.transaction():
            await c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(user), "role": "authenticated"}),),
            )
            for claim in inp.claims:
                await c.execute(
                    "insert into product_claims (org_id, product_id, claim, claim_type, "
                    "evidence_url, created_by) values (%s, %s, %s, 'verified', %s, 'human')",
                    (org, product, claim, inp.evidence_url),
                )

    async def _load(self, c: Any, org: UUID, requests: Sequence[str]) -> SeededOrg:
        cur = await c.execute(
            "select user_id from org_members where org_id = %s and role = 'owner'", (org,)
        )
        submitter: UUID = (await cur.fetchone())[0]
        cur = await c.execute("select id from brands where org_id = %s", (org,))
        brand: UUID = (await cur.fetchone())[0]
        products: dict[str, UUID] = {}
        for rid in requests:
            key = self._index[rid].product_key
            cur = await c.execute(
                "select id from products where org_id = %s and name = %s",
                (org, self._inputs[rid].product),
            )
            row = await cur.fetchone()
            if row is None:
                raise GenerationStopped(f"organisation {org}: product for {rid} is missing")
            products[key] = row[0]
        return SeededOrg(org, submitter, brand, products)

    # ── the Runner protocol ──────────────────────────────────────────────────────────────────

    def _match(self, item: Item) -> tuple[RequestInputs, RequestMeta]:
        return self._inputs[item.request_id], self._index[item.request_id]

    async def start(self, item: Item) -> str:
        inp, meta = self._match(item)
        seeded = await self.ensure_seeded(meta.brand_id, item.arm)
        body = {
            "org_id": str(seeded.org_id),
            "product_id": str(seeded.products[meta.product_key]),
            "brief": inp.brief,
            "audience": inp.audience,
            "depth": ASKED_DEPTH[item.arm],
            "depth_policy": "necessity" if item.arm == "proposed" else "requested",
            "arm": item.arm,
        }
        headers = {"Authorization": f"Bearer {self._token_for(seeded.submitter_id)}"}
        resp = await self._client.post("/api/v1/runs", json=body, headers=headers)
        if resp.status_code != 202:
            raise GenerationStopped(
                f"{item.key}: POST /runs answered {resp.status_code}: {resp.text[:300]}"
            )
        run_id = str(resp.json()["run_id"])
        self._by_run_id[run_id] = (meta.brand_id, item.arm)
        return run_id

    async def _runs(self, request_id: str, arm: str) -> list[tuple[UUID, str, Any, datetime]]:
        """Every run for (request, arm), newest first: (id, status, failure, created_at)."""
        inp, meta = self._inputs[request_id], self._index[request_id]
        org_name = self.org_name(meta.brand_id, arm)
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as c:
            cur = await c.execute(
                "select r.id, r.status, r.failure, r.created_at from runs r "
                "join organisations o on o.id = r.org_id "
                "join products p on p.id = r.product_id "
                "where o.name = %s and r.arm = %s and p.name = %s and r.request->>'brief' = %s "
                "order by r.created_at desc",
                (org_name, arm, inp.product, inp.brief),
            )
            return list(await cur.fetchall())

    async def find_orphan(self, item: Item, since: datetime) -> str | None:
        runs = await self._runs(item.request_id, item.arm)
        newest = [r for r in runs if r[3] >= since - SKEW]
        return str(newest[-1][0]) if newest else None  # the earliest since the intent

    async def status(self, run_id: str) -> tuple[Outcome, str | None]:
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as c:
            cur = await c.execute("select status, failure from runs where id = %s", (run_id,))
            row = await cur.fetchone()
        if row is None:
            raise GenerationStopped(f"run {run_id} is not in the database")
        return classify(row[0], row[1])

    async def wait(self, run_id: str) -> tuple[Outcome, str | None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait_timeout_s
        while True:
            outcome, failure = await self.status(run_id)
            if outcome != "running":
                return outcome, failure
            if loop.time() >= deadline:
                return "running", None  # the worker is gone: the driver records it `interrupted`
            await asyncio.sleep(self._poll_s)

    async def decided(self, arm: str, position: int) -> bool:
        """Has EVERY in-scope run of this arm at this position had its human decision?

        A run counts as decided when its latest approval is `approve` or `reject` (`revise` is not
        final: the campaign comes back), or when it ended without ever parking (blocked, partial,
        failed on a result): there is no human decision to wait for, and memory is written on
        approval, so nothing is owed. A request with no run yet, or whose runs were all
        invalidated and await a re-run, is NOT decided."""
        for rid in self._scope:
            if self._index[rid].position != position:
                continue
            if not await self._request_decided(rid, arm):
                return False
        return True

    async def _request_decided(self, request_id: str, arm: str) -> bool:
        for run_id, status, failure, _created in await self._runs(request_id, arm):
            outcome, _kind = classify(status, failure)
            if outcome == "invalidated":
                continue
            if outcome == "ended":
                return True
            return await self._latest_decision(run_id) in ("approve", "reject")
        return False

    async def _latest_decision(self, run_id: UUID) -> str | None:
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as c:
            cur = await c.execute(
                "select a.outcome from approvals a join campaigns c on c.id = a.campaign_id "
                "where c.run_id = %s and not a.is_calibration order by a.decided_at desc limit 1",
                (run_id,),
            )
            row = await cur.fetchone()
        return None if row is None else str(row[0])

    def approver_token(self) -> str:
        """For the DRY RUN's stand-in approver only (scripts/evaluation_dry_run.py). The generation
        run itself never calls this."""
        return self._token_for(self._approver_id)
