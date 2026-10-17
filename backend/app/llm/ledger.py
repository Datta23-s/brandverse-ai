"""The call ledger: llm_calls is the only record of model use, and every budget decision is a
query of it (Rules.md A1: every reported number traces to a raw log; no shadow counter).

Admission (before every call), in ONE short transaction holding three advisory locks — the
organisation's, the run's, then the model's, always in that order (so no deadlock cycle can form).
Admissions in different organisations on different models never wait for each other; admissions
in one organisation serialise (a race-free monthly cap needs that), never the model calls:
1. read usage from llm_calls — the run's calls and list-price cost (Rules.md C3), the
   organisation's list-price cost this UTC calendar month against
   organisations.monthly_budget_cap_usd (Architecture.md §9, "Organisation budget"), the
   model's calls since the last daily reset (C9) and in the last 60 seconds (its RPM, C10);
2. refuse — writing a 'refused' row with the reason (C9: "log the reason in route_reason") — or
   ask the caller to wait for the RPM window, or
3. write the call's row as 'pending' — with its prompt's id and version (0014) — and return its id.
The pending row IS the reservation: a concurrent admission sees it, so two calls cannot both pass
a check that only one of them should. The row is completed after the call (`finish`).

Windows are computed by Postgres on its own clock (`now()`), in the time zone the registry names
for the daily reset (config/models.yaml `rpd_resets_at`), so app and database cannot disagree.

Abandoned reservations: a 'pending' row older than `pending_expiry_s` (its process died between
admission and finish) is not counted, so a crash cannot tighten a cap for ever. The row stays in
the log and `daily_report` reports it as abandoned. Caveat: an abandoned call may have reached
the provider, so excluding it can undercount real usage by one call per crash.

Known limitation: Google counts quota per Google Cloud project, across every client using the key.
This ledger counts what is logged in THIS database; calls made with the same key elsewhere (another
database, AI Studio) are invisible to it.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.errors import BudgetExceeded, CallRefused, QuotaExhausted
from app.llm.prompts import PromptRef
from app.llm.registry import ModelRegistry, Tier

log = logging.getLogger(__name__)

# Advisory-lock keys: hashtextextended('llm:run:<id>') and ('llm:model:<id>'), taken in that order.
_LOCK_SQL = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"

Outcome = Literal["ok", "invalid_output", "error", "rate_limited"]


@dataclass(frozen=True, slots=True)
class CallContext:
    org_id: UUID
    run_id: UUID
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class Admission:
    """Either a reservation (`call_id`) or how long to wait for the model's RPM window."""

    call_id: int | None
    wait_s: float = 0.0


@dataclass(frozen=True, slots=True)
class Usage:
    run_calls: int
    run_cost_usd: float
    run_pending: int
    model_calls_today: int
    model_calls_last_minute: int
    seconds_until_minute_slot: float
    org_month_cost_usd: float
    org_month_pending: int
    org_monthly_cap_usd: float


def reset_zone(registry: ModelRegistry) -> str:
    """'00:00 America/Los_Angeles' -> 'America/Los_Angeles' (only midnight resets are supported)."""
    time_part, _, zone = registry.rpd_resets_at.partition(" ")
    if time_part != "00:00" or not zone:
        raise ValueError(f"unsupported rpd_resets_at: {registry.rpd_resets_at!r}")
    return zone


_USAGE_SQL = """
with bounds as (
  select date_trunc('day', now() at time zone %(zone)s) at time zone %(zone)s as day_start,
         date_trunc('month', now() at time zone 'UTC') at time zone 'UTC' as month_start
),
counted as (
  select c.org_id, c.run_id, c.model, c.cost_usd, c.outcome, c.created_at,
         c.created_at >= b.day_start as today, c.created_at >= b.month_start as this_month
  from public.llm_calls c, bounds b
  where c.outcome <> 'refused'
    and not (c.outcome = 'pending' and c.created_at <= now() - %(expiry)s * interval '1 second')
    and (c.run_id = %(run)s
         or (c.org_id = %(org)s and c.created_at >= b.month_start)
         -- the RPM window can reach back past the daily reset
         or (c.model = %(model)s
             and c.created_at >= least(b.day_start, now() - interval '60 seconds')))
)
select
  count(*) filter (where run_id = %(run)s),
  coalesce(sum(cost_usd) filter (where run_id = %(run)s), 0)::float8,
  count(*) filter (where run_id = %(run)s and outcome = 'pending'),
  count(*) filter (where model = %(model)s and today),
  count(*) filter (where model = %(model)s and created_at > now() - interval '60 seconds'),
  coalesce(extract(epoch from (
    min(created_at) filter (where model = %(model)s and created_at > now() - interval '60 seconds')
    + interval '60 seconds' - now())), 0)::float8,
  coalesce(sum(cost_usd) filter (where org_id = %(org)s and this_month), 0)::float8,
  count(*) filter (where org_id = %(org)s and this_month and outcome = 'pending'),
  (select monthly_budget_cap_usd::float8 from public.organisations where id = %(org)s)
from counted
"""

_INSERT_SQL = """
insert into public.llm_calls
  (org_id, run_id, agent_id, model, tier, prompt_id, prompt_version, route_reason, outcome)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
returning id
"""


def _row(
    ctx: CallContext, model: str, tier: Tier, prompt: PromptRef, reason: str, outcome: str
) -> tuple[object, ...]:
    return (
        ctx.org_id,
        ctx.run_id,
        ctx.agent_id,
        model,
        tier.value,
        prompt.prompt_id,
        prompt.prompt_version,
        reason,
        outcome,
    )


class CallLedger:
    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection],
        registry: ModelRegistry,
        *,
        max_calls_per_run: int,
        max_cost_per_run_usd: float,
        pending_expiry_s: int,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._max_calls = max_calls_per_run
        self._max_cost = max_cost_per_run_usd
        self._expiry_s = pending_expiry_s
        self._zone = reset_zone(registry)

    async def usage(self, conn: AsyncConnection, ctx: CallContext, tier: Tier) -> Usage:
        model = self._registry.model(tier)
        cur = await conn.execute(
            _USAGE_SQL,
            {
                "org": ctx.org_id,
                "run": ctx.run_id,
                "model": model.id,
                "zone": self._zone,
                "expiry": self._expiry_s,
            },
        )
        row = await cur.fetchone()
        if row is None:  # an aggregate always returns a row
            raise RuntimeError("usage query returned nothing")
        if row[8] is None:
            raise RuntimeError(f"organisation {ctx.org_id} not found")
        return Usage(
            int(row[0]),
            float(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            float(row[5]),
            float(row[6]),
            int(row[7]),
            float(row[8]),
        )

    async def admit(
        self,
        ctx: CallContext,
        tier: Tier,
        prompt: PromptRef,
        route_reason: str,
        estimated_cost_usd: float,
    ) -> Admission:
        model = self._registry.model(tier)
        refusal: CallRefused | None = None
        async with self._pool.connection() as conn, conn.transaction():
            # Org, run, model: one fixed order. Separate statements, so the order is guaranteed.
            await conn.execute(_LOCK_SQL, (f"llm:org:{ctx.org_id}",))
            await conn.execute(_LOCK_SQL, (f"llm:run:{ctx.run_id}",))
            await conn.execute(_LOCK_SQL, (f"llm:model:{model.id}",))
            u = await self.usage(conn, ctx, tier)
            # Pending calls have no cost yet; count each at this call's estimate.
            projected = u.run_cost_usd + (u.run_pending + 1) * estimated_cost_usd
            org_projected = u.org_month_cost_usd + (u.org_month_pending + 1) * estimated_cost_usd
            if u.run_calls >= self._max_calls:
                refusal = BudgetExceeded(
                    f"refused: run has made {u.run_calls} of {self._max_calls} LLM calls "
                    "(Rules.md C3)"
                )
            elif projected > self._max_cost:
                refusal = BudgetExceeded(
                    f"refused: run cost would reach ${projected:.4f} of ${self._max_cost:.2f} "
                    "list-price cap (Rules.md C3)"
                )
            elif org_projected > u.org_monthly_cap_usd:
                refusal = BudgetExceeded(
                    f"refused: organisation month-to-date cost would reach ${org_projected:.4f} "
                    f"of its ${u.org_monthly_cap_usd:.2f} monthly cap "
                    "(organisations.monthly_budget_cap_usd, UTC calendar month)"
                )
            elif u.model_calls_today >= model.rate_limits.rpd:
                refusal = QuotaExhausted(
                    f"refused: {model.id} has used {u.model_calls_today} of "
                    f"{model.rate_limits.rpd} requests since the daily reset (Rules.md C9)"
                )
            elif u.model_calls_last_minute >= model.rate_limits.rpm:
                # Not a refusal: pace to the RPM window (a wait, Rules.md C10). Nothing written.
                return Admission(call_id=None, wait_s=max(u.seconds_until_minute_slot, 0.05))

            reason = str(refusal) if refusal else route_reason
            outcome = "refused" if refusal else "pending"
            cur = await conn.execute(
                _INSERT_SQL, _row(ctx, model.id, tier, prompt, reason, outcome)
            )
            row = await cur.fetchone()
        if refusal is not None:
            log.warning("LLM call refused", extra={"reason": str(refusal), "tier": tier.value})
            raise refusal
        if row is None:
            raise RuntimeError("insert returned no id")
        return Admission(call_id=int(row[0]))

    async def refuse(self, ctx: CallContext, tier: Tier, prompt: PromptRef, reason: str) -> None:
        """Record a refusal decided outside admission (e.g. an RPM wait over its cap)."""
        model = self._registry.model(tier)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_INSERT_SQL, _row(ctx, model.id, tier, prompt, reason, "refused"))

    async def finish(
        self,
        call_id: int,
        *,
        outcome: Outcome,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float | None,
        latency_ms: int | None,
        rate_limit_wait_ms: int,
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update public.llm_calls set outcome = %s, input_tokens = %s, output_tokens = %s, "
                "cost_usd = %s, latency_ms = %s, rate_limit_wait_ms = %s, finished_at = now() "
                "where id = %s and outcome = 'pending'",
                (
                    outcome,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    rate_limit_wait_ms,
                    call_id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"llm_calls row {call_id} was not pending")

    async def daily_report(self, day: date | None = None) -> list["ModelDay"]:
        """Per model, for one reset day (midnight in the registry's zone; default: today).

        Refusals are reported beside the calls (Rules.md A2: capped work must not vanish), and
        abandoned reservations apart from live ones. Read from llm_calls only.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                _REPORT_SQL, {"day": day, "zone": self._zone, "expiry": self._expiry_s}
            )
            rows = await cur.fetchall()
        return [ModelDay(*row) for row in rows]


@dataclass(frozen=True, slots=True)
class ModelDay:
    model: str
    tier: str
    ok: int
    invalid_output: int
    error: int
    rate_limited: int
    pending: int
    abandoned: int
    refused: int
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @property
    def calls(self) -> int:
        """Calls counted against caps and quota: everything but refusals and abandoned rows."""
        return self.ok + self.invalid_output + self.error + self.rate_limited + self.pending


_REPORT_SQL = """
with bounds as (
  select coalesce(%(day)s::date, (now() at time zone %(zone)s)::date)::timestamp
         at time zone %(zone)s as day_start
)
select model, tier,
  count(*) filter (where outcome = 'ok'),
  count(*) filter (where outcome = 'invalid_output'),
  count(*) filter (where outcome = 'error'),
  count(*) filter (where outcome = 'rate_limited'),
  count(*) filter (where outcome = 'pending'
                   and created_at > now() - %(expiry)s * interval '1 second'),
  count(*) filter (where outcome = 'pending'
                   and created_at <= now() - %(expiry)s * interval '1 second'),
  count(*) filter (where outcome = 'refused'),
  coalesce(sum(input_tokens), 0)::int,
  coalesce(sum(output_tokens), 0)::int,
  coalesce(sum(cost_usd), 0)::float8
from public.llm_calls, bounds
where created_at >= bounds.day_start and created_at < bounds.day_start + interval '1 day'
group by model, tier
order by model
"""
