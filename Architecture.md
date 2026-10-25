# BrandverseAI — Architecture

| Field | Value |
|---|---|
| Document | Architecture.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Depends on | `PRD.md` |

---

## 1. Architectural stance

Three commitments decide every structural choice:

1. **Deterministic orchestration, adaptive execution.** The graph of steps is fixed and auditable. What varies is *depth*: which agents run, how much research happens, which model answers. An LLM never decides the control flow of the pipeline — it decides content within a step. This is what makes runs reproducible and debuggable.
2. **Evidence travels with the claim.** A sentence of ad copy is never separated from the source that justifies it. Provenance is a first-class column, not a log line.
3. **Cost is a pre-flight constraint.** Budget is checked before spending, not reported after.

---

## 2. Layer map

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. USERS — Marketers │ Analysts │ Business Users │ Admins        │
├──────────────────────────────────────────────────────────────────┤
│ 2. WEB APPLICATION — Next.js 15 + TypeScript + React 19 (Vercel) │
├──────────────────────────────────────────────────────────────────┤
│ 3. API GATEWAY — FastAPI (laptop)                                │
├────────────────────────────┬─────────────────────────────────────┤
│ 4a. AUTHORIZATION          │ 4b. JOB MANAGEMENT                  │
│     Supabase Auth + RLS    │     Redis + ARQ worker queue        │
├────────────────────────────┴─────────────────────────────────────┤
│ 5. AGENT ORCHESTRATOR — LangGraph 1.2 StateGraph                 │
├─────────────────┬──────────────────┬─────────────────────────────┤
│ 6a. MEMORY      │ 6b. RESEARCH     │ 6c. LLM                     │
│ Postgres +      │ SearXNG +        │ LiteLLM gateway →           │
│ pgvector        │ Trafilatura      │ Gemini 3.x / OSS fallback   │
├─────────────────┴──────────────────┴─────────────────────────────┤
│ 7. SPECIALIST AGENTS — A1 … A7                                   │
├──────────────────────────────────────────────────────────────────┤
│ 8. VALIDATION ENGINE — claim check, brand voice, risk            │
├──────────────────────────────────────────────────────────────────┤
│ 9. CAMPAIGN INTELLIGENCE — assembly + scoring                    │
├──────────────┬──────────────────┬────────────────────────────────┤
│ 10a. Reports │ 10b. Creative    │ 10c. Analytics                 │
├──────────────┴──────────────────┴────────────────────────────────┤
│ 11. SUPABASE — Postgres + Storage + Auth (unified data store)    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Technology selection and justification

| Layer | Choice | Why this, not the alternative |
|---|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind | Server components reduce client JS; Vercel deploy is free at this scale |
| 3D | Three.js r17x via react-three-fiber + drei | Declarative scene graph composes with React state; raw Three.js would fight the component model |
| Backend | FastAPI, Python 3.12 | The agent and ML ecosystem is Python; async-native; automatic OpenAPI schema |
| Orchestration | **LangGraph 1.2.x** | Explicit graph with durable checkpoints. A plain async function chain cannot resume a failed 5-minute run; LangGraph can. |
| LLM gateway | **LiteLLM** | One OpenAI-compatible surface over every provider, with native per-key budgets and fallback chains — exactly the cost governance the PRD demands |
| Models | **Gemini 3.x family** | Cost/quality fit and a 1M-token context for long research packs |
| Database | Supabase Postgres + pgvector | Relational data, vectors, auth and storage in one managed service. Separate vector DB would add a second consistency problem for no benefit at this scale. |
| Search | Self-hosted SearXNG | Metasearch over many engines, JSON API, no per-query cost, no vendor lock |
| Extraction | Trafilatura | Highest-accuracy open main-content extractor; strips boilerplate that would poison embeddings |
| Queue | Redis + ARQ | Runs exceed HTTP timeout; jobs must survive a dyno restart |

### 3.1 Why not a simpler architecture

A single prompt with web search would produce *a* campaign. It would not produce a campaign that costs less the second time, cites its sources, or refuses to invent a product feature. Those three properties require memory, provenance and validation respectively, and each needs real infrastructure. **This is the argument for the whole build — rehearse it.**

---

## 4. Model layer and routing

> ### Model selection and drift
> Gemini 3.x is pinned for better cost/capability than 2.5 — **not** because 2.5 has an announced shutdown (as of 14 Oct 2026, Google's deprecations page lists no end date for it; an earlier draft of this document wrongly stated one, from a stale search result rather than the live page). Provider deprecation pages and prices change without much notice. Verify current IDs, prices and shutdown dates at `ai.google.dev` before every deploy, and never hardcode a model ID outside `config/models.yaml`.

### 4.1 Routing tiers

All tiers run on the Gemini API **free tier** (`Rules.md` C0). Rate limits were read from the
project's AI Studio account on 2026-10-14 and are hard constraints; IDs, limits and prices live
only in `config/models.yaml`.

| Tier | Model (Oct 2026) | Used for | Free tier RPM / TPM / RPD | List price in / out per 1M (not paid) |
|---|---|---|---|---|
| **Cheap** | `gemini-3.1-flash-lite` | Classification, summarisation, query rewriting, metadata extraction, relevance pre-filter | 15 / 250K / 500 | $0.25 / $1.50 |
| **Standard** | `gemini-3.5-flash-lite` | Product intelligence, persona analysis, campaign planning, most agent work | 15 / 250K / 500 | $0.30 / $2.50 |
| **Strong** | `gemini-3.8-flash` | Conflicting evidence, high-risk claims, final strategy arbitration | **5 / 250K / 20** | $0.75 / $3.75 (from 2027-01-01: $1.50 / $7.50) |
| **Embedding** | `gemini-embedding-2` | Memory and retrieval embeddings, 768 dims | 100 / 30K / 1000 | $0.20 / — |
| **Fallback** | OSS model via LiteLLM (Hugging Face / local) | Provider outage, quota exhaustion | — | — |

**Three tiers, not four.** No Pro model is available on the free tier (every Pro model, including
`gemini-3.1-pro` and `gemini-2.5-pro`, is 0 / 0 / 0), so there is no fourth, stronger tier. A
fourth "strong-with-retry" tier was rejected: it is the same model as Strong, adds no capability,
and its retries would spend the scarcest quota (20 requests/day); retries are governed by
`Rules.md` C8, not by a tier. `PRD.md` M6 asks for at least three cost tiers.

**No media tier.** Image-generation models are listed only for paid tiers, so no image is
generated: A6 produces poster prompts as text and `max_media_generations` is effectively 0. This
conflicts with Phase 8's "generated images render in the campaign report"; that criterion cannot
be met at zero cost.

> ### Stated limitation — the strong tier is a Flash model
> The routing design assumed a large capability gap between Standard (Flash) and Strong (Pro).
> On the free tier Strong is `gemini-3.8-flash`, a newer Flash model, against a Flash-Lite
> Standard: the gap is much smaller than designed. `Rules.md` T4 still routes regulated-sector
> work to Strong, but that buys less extra correctness than the design assumed. This **weakens the
> model-routing part of the Phase 7 contribution**: any quality or safety difference attributed to
> routing is bounded by a narrow tier gap, and a null routing effect may reflect the free-tier
> model line-up rather than the routing policy. It is reported as a limitation
> (`docs/evaluation-plan.md` §4), not hidden. The registry makes the tier a one-line change, so a
> paid Pro tier could be evaluated later without code changes — outside this project's
> zero-cost constraint.

### 4.2 Routing policy

Route on five signals, in this order of precedence:

```python
def route(task) -> Tier:
    if task.risk == "high" or task.has_conflicting_evidence or task.t4_required:
        tier = Tier.STRONG            # correctness outranks cost
    elif task.complexity == "trivial" or task.is_structural:
        tier = Tier.CHEAP
    elif budget.remaining_usd < budget.reserve_floor:
        tier = Tier.CHEAP             # degrade rather than abort
    elif task.prior_failures >= 1:
        tier = escalate(task.last_tier)
    else:
        tier = Tier.STANDARD
    return daily_quota.admit(task, tier)   # Rules.md C9: may downgrade or defer, with a reason
```

**No agent defaults to Strong** (`Rules.md` C5: start at the cheapest tier that can do the job;
escalate, do not start high). Strong is reached only through the first branch:

- `task.t4_required` — `Rules.md` T4, by function: the call performs **claim verification or risk
  assessment on regulated-sector output** (health, finance, children's products), whichever agent
  performs it;
- `task.risk == "high"` — a high-risk claim, in any sector;
- `task.has_conflicting_evidence` — sources disagree on a fact the output relies on.

All other calls in a regulated-sector run keep their default tier, and the sector constraint
registry applies to everything regardless of tier. The rule names a function, not an agent, so it
holds unchanged when A4 ships or agents are renumbered. In practice A7 (and A4, where built) runs
on Standard in an ordinary run and escalates call by call.

`daily_quota.admit` applies the per-day budget (`Rules.md` C9). It counts every call per model
since the last RPD reset (midnight Pacific; limits are per Google Cloud project, so development and
test calls count too). A call that would breach its model's RPD is **downgraded** to the next lower
tier with available quota, and the downgrade and its reason are written to `llm_calls.route_reason`
— the run degrades, it does not fail. **Exception:** a call that `Rules.md` T4 or the high-risk rule
requires on Strong is **deferred** until the quota resets, not downgraded, because a downgraded
mandatory safety check would break T4; the run waits with a recorded note. If every tier is
exhausted, the run ends as a partial result flagged `budget_exhausted` (`Rules.md` C4).

Every routing decision is persisted to `llm_calls` with its reason. The evaluation harness reads that table directly.

### 4.3 Rate-limit-aware scheduling

Free-tier RPM limits are low (Strong 5, Standard and Cheap 15, Embedding 100 per minute), and the
Phase 6 parallel agent fan-out would exceed them. All LLM and embedding calls go through one
scheduler per model that holds a token bucket sized to that model's RPM and TPM, so concurrent
agents queue rather than collide. A `429` that still occurs is waited out — honouring the
`retryDelay` Google returns, otherwise exponential backoff with jitter — and retried.

**A rate-limit wait is not a failure.** Waiting on a `429` does not count toward `Rules.md` C8's
cap of two retries for failed calls, and the retried request is the same request, not a new prompt.
Each call records its rate-limit wait time separately from its latency, so the evaluation can
report latency with and without free-tier waits (`docs/evaluation-plan.md` §4). A wait that would
push the run past its latency budget ends the step as a partial result, never a crash.

### 4.4 The call path as built (Phase 2 chunk A, 2026-10-17)

- **One gateway** (`backend/app/llm/gateway.py`): the only code that calls a model
  (`Rules.md` P6). LiteLLM is used **as a library** (`litellm.acompletion`, imported only through
  `app/llm/_litellm.py`), not as the LiteLLM proxy: the proxy's per-key budgets would be a second
  counter beside `llm_calls`, which the budget rule below forbids. `LITELLM_MASTER_KEY` (a proxy
  setting, §11) is therefore unused. LiteLLM's import-time network fetch of its price map is
  switched off; its prices are not used.
- **Tiers, not models, in code.** Code names `cheap` / `standard` / `strong`; the registry
  (`app/llm/registry.py`) reads `config/models.yaml` and derives LiteLLM's model string
  (`<provider>/<id>`). Switching a tier's model is one edited line; `test_llm_registry.py` edits
  exactly one line and checks the call goes to the new model, and fails if any model ID appears
  in code. Cost is computed from the registry's list prices, including dated price changes.
- **Structured output, always.** `structured(schema, ...)` returns a validated Pydantic instance,
  never a string. Output that does not validate gets **one** repair call — a different prompt that
  quotes the validator's errors (`repair-v1`) — then `StructuredOutputInvalid` is raised. At most
  two calls per request (`Rules.md` C8).
- **No hidden retries.** LiteLLM's and the HTTP client's retries are off on every call. A `429` is
  waited out (Google's `retryDelay`, else exponential backoff with jitter) and the same request
  re-sent, at most `llm_max_rate_limit_waits` times and `llm_max_rate_limit_wait_s` seconds in
  total; then the call ends `rate_limited`. Any other provider error ends `error` and is not retried.
- **Budgets are queries of `llm_calls`, never a counter** (`Rules.md` A1; `app/llm/ledger.py`).
  Before each call, one short transaction under an advisory lock reads the run's calls and cost
  (C3), the model's calls since the daily reset — midnight in the zone `models.yaml` names,
  computed by Postgres — (C9) and in the last 60 s (RPM, C10), then refuses (writing a `refused`
  row with the reason), asks the caller to wait for the RPM window, or writes the call's row as
  `pending`. The pending row is the reservation, so concurrent calls cannot both pass a check that
  only one should (`test_llm_ledger.py` fails without the lock). The row is completed with tokens,
  list-price cost, latency, `rate_limit_wait_ms` and outcome when the call returns (migration 0013).
- **Stated limitations.** Google counts quota per Cloud project across every client of the key;
  the ledger sees only calls logged in its own database. Pending calls in a run count toward its
  cost cap at the current call's estimate. An exhausted daily quota fails the run instead of
  degrading it — a divergence from `Rules.md` C4 and C9, stated below. Importing LiteLLM adds about 14 s to a Python process's start (measured 2026-10-17).
- **Admission locks: per organisation, per run and per model, nothing broader.** Three
  transaction-scoped advisory locks, org then run then model (one fixed order, so no deadlock).
  They are held only for the admission transaction — one aggregate query and one insert — never
  during the model call. Parallel agents in one run serialise their admissions but call the model
  concurrently. Admissions in different organisations on different models never wait for each
  other (`test_locks_are_per_org_run_and_model_not_global`). The org lock (added 2026-10-17 for the
  monthly cap below) means admissions across runs of ONE organisation serialise too — every
  organisation has a cap (`monthly_budget_cap_usd` is NOT NULL), so a race-free cap needs it. The
  model lock has no day component: only the current day is ever admitted, so it is the same lock
  in effect. Accepted cost: admission throughput per organisation and per model is bounded by that
  short transaction — far above the free tier's 15 RPM.
- **Organisation monthly cap — enforced at admission** (2026-10-17; it was documented in §9,
  "Organisation budget and rate limits", but not built until then). The org's list-price cost this
  UTC calendar month, from `llm_calls` (refusals and abandoned reservations excluded; pending calls
  at this call's estimate), plus this call's estimate must fit `organisations.monthly_budget_cap_usd`.
  A breach is `BudgetExceeded`, logged as a refusal, and ends the run `partial` with
  `budget_exhausted` exactly as the run caps do. Tested in `test_llm_ledger.py` (whole org, current
  month only, refusals/abandoned/other orgs ignored, 30 concurrent runs) and
  `test_budgeted_run.py`.
- **Abandoned reservations expire after 900 s** (`llm_pending_expiry_s`). A `pending` row older
  than that stops counting toward caps and quotas, so a crashed process cannot tighten a cap for
  ever; it stays in the log and is reported as abandoned. 900 s, not less: a live call can stay
  pending at most (waits + 1) × timeout + total wait = (3 + 1) × 60 + 90 = 330 s at the defaults,
  and `Settings` refuses an expiry at or below that bound, so an in-flight call is never miscounted
  as abandoned. Not more: a crash then costs at most 15 minutes of one slot. Caveat: an abandoned
  call may have reached Google, so excluding it can undercount real usage by one call per crash.

#### Development and smoke calls — invisible to the live ledger

Calls made while developing and smoke-testing are logged to a **throwaway local database**
(`scripts/e2e_prepare_db.py`), never to the live project, whose `llm_calls` and `runs` rows can
never be deleted (0010, 0011). The live ledger cannot see them, yet Google counts them against the
same per-project quota (§4.3), so the live daily count can run below actual usage. They are
therefore tallied here by hand, and every live-verification call is added to this table:

| Date (UTC) | Script | Database | cheap | standard | strong |
|---|---|---|---|---|---|
| 2026-10-17 10:35 | `scripts/llm_smoke.py` (cap 2) | local `brandverse_e2e` | 1 | 0 | 0 |
| 2026-10-18 02:22–02:29 | `scripts/v2_probe.py` (cap 10; approved: 10 strong) | local `brandverse_v2_probe` | 0 | 0 | 10 |
| 2026-10-18 02:58–11:07 | `scripts/schema_confirm.py` (cap 3 across runs; approved: 3 cheap) | local `brandverse_v2_probe` | 3 | 0 | 0 |
| **Total** | | | **4** | **0** | **10** |

2026-10-18, V2 probe: all ten strong-tier calls ended `error` and produced no result — eight
`400 INVALID_ARGUMENT`, one client timeout at 90 s, one `503 UNAVAILABLE`. Whether Google counted
the failed requests against the 20/day strong quota is not known; the tally assumes it did. The
script ran on after the first `400`, a systematic error; it should have stopped there
(`evaluation/probes/v2_adversarial_2026-10-18.json`; `docs/evaluation-plan.md`, V2).

2026-10-18, schema fix confirmation (`app/llm/schema.py`), all on `gemini-3.1-flash-lite`:
call 1, classifier schema with nullable type lists and `maxItems` — `400`; call 2, control
(yesterday's flat smoke request through today's gateway) — `ok`, 19 in / 27 out; call 3, classifier
schema with nullables optional and no item counts — `ok`, 343 in / 82 out, 2,343 ms. The budget of
three was not exceeded.

Counts are read from the throwaway database's `llm_calls`, not from the script. 2026-10-17: one
`ok` row on `gemini-3.1-flash-lite`, valid on the first call (no repair); 19 input / 27 output
tokens, $0.000045 list-price equivalent, latency 12,265 ms, no rate-limit wait; the row was
completed 24 ms after the call returned.

The key belongs to a dedicated Google Cloud project (`brandverse-ai`) that nothing else uses
(stated by the user 2026-10-17), so the only calls the live ledger cannot see are the ones in this
table. The same day the user re-read the free-tier limits for that project: 15/500 (cheap),
15/500 (standard), 5/20 (strong), 100/1000 (embedding) RPM/RPD — identical to `config/models.yaml`.

**No development, test or smoke call ever reaches the strong tier, on any database.** Enforced, not
left to discipline: the gateway refuses the strong tier unless `llm_allow_strong_tier` is set
(default false; set only for evaluation runs), before admission and before any provider call, and
records the refusal (`test_the_strong_tier_is_refused_unless_explicitly_allowed`). The smoke script
constructs its gateway with the strong tier off whatever the settings say.

#### Prompts — versioned templates, recorded per call (Phase 2 chunk B, 2026-10-17)

- **Templates are files**, `config/prompts/<prompt_id>/<version>.yaml`, each naming its own id
  and version (which must match its path), its variables and its system/user text, with
  `string.Template` placeholders (`$name`) — no inline f-strings, no expressions. The gateway
  accepts only a `RenderedPrompt` (`app/llm/prompts.py`); callers name the version explicitly
  (there is no "latest"). The repair prompt is a template too (`structured_repair@v1`).
- **A released version is immutable.** `config/prompts/versions.lock` holds each version file's
  SHA-256 (line endings normalised); the library refuses to load if a file is edited, unlocked
  or missing. A change is a new version file, a new lock line and a caller naming it. Stated
  limitation: someone could edit a file *and* its lock line; that is a deliberate act visible in
  the diff, not an accident.
- **Every `llm_calls` row records `prompt_id` and `prompt_version`** (migration 0014, NOT NULL),
  refusals included. A repair call records the repair template; its `route_reason` names the
  prompt it repaired.
- **A mid-evaluation prompt change is detectable.** `app/evaluation/prompt_versions.py` reads
  `llm_calls` joined to `runs.arm` for the evaluation organisations (practice organisations
  excluded), optionally bounded by time, and flags a prompt used in more than one version within
  an arm (`mixed_within_arm`) or in different versions by arms that both use it
  (`differs_across_arms`). Refusals count: they show what the code would have sent.
  Tested in `tests/db/test_prompt_versions.py`.

#### Decision — the run executor and the run statuses (2026-10-17)

**Context.** `Rules.md` C4 requires a budget breach to return a partial result flagged
`budget_exhausted`, and the Phase 2 exit criterion needed a run to show it. No document defined
how a run ends or what `runs.status` may hold (0005 declares it `text not null default 'queued'`,
with no check). Both were decided in Phase 2 chunk B, by the implementation, not by the documents;
they are recorded here so they are not reinvented.

**Decision.**
- **One executor: `app/core/budgeted_run.py`.** It runs a run's LLM steps in order, each seeing the
  outputs before it. When the ledger refuses a call with `BudgetExceeded` — the run's call or cost
  cap (C3) or the organisation's monthly cap — the run stops there and returns a `RunOutcome`: the
  completed steps' outputs, the steps not run, the refusal's reason, `budget_exhausted = true`.
  It is the only code that writes a run's end (`end_run`): `status`, `budget_exhausted`,
  `completed_at`, and `llm_call_count` / `total_cost_usd` derived from `llm_calls` in the same
  statement (never a counter, `Rules.md` A1; `total_cost_usd` is rounded to its 4 decimals, so
  `llm_calls` stays the source).
- **Statuses.** `queued` (the column default, written when the run row is created), then exactly
  one of:

  | Status | Meaning | `budget_exhausted` |
  |---|---|---|
  | `completed` | Every step ran | false |
  | `partial` | Stopped by a budget refusal; the completed steps' output is kept and returned | true |
  | `failed` | Any other error, cancellation included; re-raised to the caller | false |

**Reasoning.** `partial` is separate from `completed` because C4 forbids a silent truncation and
`Rules.md` A2 requires capped runs to be counted in the evaluation, not to vanish into
"completed". It is separate from `failed` because a partial run has usable, validated output and a
failed one has none; the evaluation reports them differently. `budget_exhausted` stays a column of
its own (0005) beside the status: it says *why* a run is partial, and §4.3 already names a second
way to be partial (a step ending at the latency budget), which will have to be told apart.
`QuotaExhausted` is deliberately not a budget breach: C9 says it should downgrade or defer, which
is not built (next section), so it fails the run rather than being disguised as `partial`.

**Consequence — binding on Phase 6.** The LangGraph pipeline **must** run its LLM steps under this
executor — as `Step`s, or by ending the run through `end_run` with a `RunOutcome` — and must not
write `runs.status` itself or build a second executor (`Phases.md` Phase 6 deliverables). New
statuses (for example a running state for the UI, or one for a latency-budget stop) are added
here, in this table, before any code writes them. **Stated limitation:** `runs.status` has no
database CHECK, so nothing but this rule stops another writer using another value; a CHECK on the
four statuses is a one-line migration that should land before the evaluation's first run.

Tested with a mocked provider in `tests/db/test_budgeted_run.py`: run call cap, run cost cap,
organisation monthly cap (each `partial`, flagged), within budget (`completed`), another error
(`failed`, re-raised).

#### Known divergence from Rules.md C4 and C9 — an exhausted daily quota fails the run

**`Rules.md` C9** (approved 2026-10-14): a call that would breach its model's daily quota (RPD) is
downgraded to a lower tier with the reason logged in `llm_calls.route_reason`; only a strong-tier
call that `Rules.md` T4 requires is deferred until the quota resets; the run degrades rather than
fails. **`Rules.md` C4**: a breach returns a partial result, never a hard failure.

**What the code does (Phase 2, 2026-10-17):** the ledger refuses the call (`QuotaExhausted`,
logged as a refusal with its reason) and the executor **fails the run** (`runs.status =
'failed'`). Neither downgrade nor deferral exists. Both need the routing policy — which tier is
"lower" for a given call, and which calls T4 binds to the strong tier — and that is Phase 7
(`Phases.md` Phase 7, "Close the C4/C9 daily-quota gap"). **Until Phase 7 lands, a run that
exhausts a daily quota fails rather than degrading**, and nothing written about this project may
claim otherwise.

#### Cost visibility — measured (2026-10-17)

From the provider's reply reaching the gateway to a separate connection reading that call's
cost: **20 mocked calls against local Postgres, min 9.5 ms, median 11.3 ms, max 19.4 ms**
(`test_cost_is_in_the_database_within_one_second_of_every_call`, which asserts every interval is
under 1 s). The one real call (above) completed its row 24 ms after the reply. Production writes
go through the Supavisor session pooler instead: a read-only transaction of the same shape as
`ledger.finish()` (BEGIN, one statement, COMMIT) took **min 35 ms, median 36 ms, max 48 ms** over
20 runs from the laptop. That is a proxy measurement; no row was written to the live project.

Live tier switch, mocked provider (`scripts/demo_tier_switch.py`, 2026-10-17): one edited line
in a copy of `config/models.yaml` (`id: gemini-3.1-flash-lite` → `id: mock-switched-model`);
the provider received `gemini/gemini-3.1-flash-lite` before and `gemini/mock-switched-model`
after, the `llm_calls` rows recorded each, and the other tiers were unchanged. Rows went to their
own throwaway database (`brandverse_tier_switch_demo`), not to `brandverse_e2e`, so mocked calls
never enter the real-call tally.

---

## 5. Orchestration

### 5.1 Graph shape

```
START
  │
  ├─ normalise_request          (step 1)
  ├─ load_product_context       (step 2)
  ├─ validate_product_truth     (step 3)  ──► [blocked] ──► END
  ├─ retrieve_memory            (step 4)
  ├─ assess_research_necessity  (step 5)
  │     │
  │     ├─ D0 ──────────────────────────────┐
  │     ├─ D1 ──► focused_research          │   (step 6)
  │     └─ D2 ──► deep_research             │
  │                                          │
  ├─ select_agents  ◄───────────────────────┘  (step 7a)
  ├─ run_agents     [parallel fan-out]         (step 7b)
  ├─ personalise                               (step 8)
  ├─ build_strategy                            (step 9)
  ├─ generate_creative                         (step 10)
  ├─ validate_evidence                         (step 11)
  │     └─ [fail & rounds_left] ──► generate_creative
  ├─ await_human_approval        [interrupt]   (step 12)
  ├─ assemble_campaign                         (step 13)
  ├─ record_feedback                           (step 14)
  └─ write_memory                              (step 15)
END
```

### 5.2 State schema

```python
from typing import Annotated, Literal, TypedDict
from operator import add

class RunState(TypedDict):
    run_id: str
    org_id: str
    request: dict
    product: dict | None
    truth: dict | None                      # verified / deduced / restricted
    memory_hits: list[dict]
    memory_coverage: float                  # 0..1
    research_score: float                   # R
    depth: Literal["D0", "D1", "D2"]
    findings: Annotated[list[dict], add]    # reducer: agents append concurrently
    selected_agents: list[str]
    agent_outputs: Annotated[dict, merge_dicts]
    personas: list[dict]
    strategy: dict | None
    creative: dict | None
    validation: dict | None
    revision_round: int
    approval: dict | None
    budget: dict                            # spent, remaining, caps
    errors: Annotated[list[dict], add]
```

`findings` and `agent_outputs` use reducers because agents run concurrently. Without reducers, parallel writes to the same key raise `InvalidUpdateError` — this is the single most common LangGraph mistake.

### 5.3 Durability and human-in-the-loop

- Compile with a Postgres checkpointer: `graph = builder.compile(checkpointer=saver, interrupt_before=["await_human_approval"])`
- Every run carries `config={"configurable": {"thread_id": run_id}}`
- The approval interrupt suspends the graph. The frontend polls run status; approval resumes from the checkpoint with `graph.invoke(None, config)`. No state is held in memory between the two.
- Set `LANGGRAPH_STRICT_MSGPACK=true`.

---

## 6. Research layer

### 6.1 Research Necessity Engine

```
R = wₙN + wᵤU + w_fF + wₛS + wₑE
```

| Symbol | Meaning | Source |
|---|---|---|
| `N` | Novelty of request vs memory | 1 − max cosine similarity to prior runs |
| `U` | Uncertainty | fraction of required knowledge slots unfilled |
| `F` | Freshness requirement | from request type: pricing/competitor = high, brand voice = low |
| `S` | Sector risk | regulated sectors (health, finance) score high |
| `E` | Evidence requirement | claims needing external proof |

Default weights `wₙ=0.30, wᵤ=0.25, w_f=0.20, wₛ=0.15, wₑ=0.10`. **Weights live in `config/scoring.yaml` and must be tuned and reported in Phase 10 — publishing untuned defaults as findings is not acceptable.**

| R range | Depth | Behaviour |
|---|---|---|
| `R < 0.35` | **D0** | Reuse memory only. Zero searches. |
| `0.35 ≤ R < 0.70` | **D1** | Focused research: ≤ 6 queries targeting named gaps |
| `R ≥ 0.70` | **D2** | Deep research: ≤ 20 queries, multi-hop, competitor sweep |

### 6.2 Pipeline

`query planning → SearXNG (JSON) → dedupe by canonical URL → fetch → Trafilatura extract → source scoring → chunk → embed → stage as findings`

**Source scoring** combines domain authority, recency and specificity to the query (`backend/app/research/source_scoring.py`, weights and threshold in `config/scoring.yaml` `source:`). **Corroboration count is not modelled** (decided 2026-10-25, `Memory.md` §3.1): it would need a bonus with no principled value, and is future work. An item backed by several pages carries the maximum of their scores. Findings below threshold are discarded, not passed to agents — unscored sources are how hallucinated authority enters a system.

**Zero-retention.** Raw fetched page bodies live in Redis with a TTL and are never written to Postgres. Only extracted, scored, attributed findings persist. This is the implementable core of the paper's privacy boundary.

---

## 7. Specialist agents

All seven share one contract:

```python
class Agent(Protocol):
    id: str
    name: str
    required_inputs: list[str]
    output_schema: type[BaseModel]       # Pydantic, strictly validated
    default_tier: Tier
    async def run(self, state: RunState, ctx: AgentContext) -> BaseModel: ...
```

| ID | Agent | Produces | Default tier | Phase |
|---|---|---|---|---|
| **A1** | Product & Brand Intelligence | Positioning, differentiators, brand voice vector, claim inventory | Standard | 6 |
| **A2** | Market & Advertisement Intelligence | Competitor ads, category conventions, whitespace | Standard | 6 (live research); 9 (persistent ad repository) |
| **A3** | Customer Behaviour Intelligence | Personas, jobs-to-be-done, objections, channel affinity | Standard | 6 |
| **A4** | Sector & Evidence Intelligence | Sourced proof, evolving regulatory context. *Required disclaimers and prohibited phrasings are not A4's: they are rules in the sector constraint registry (§8.1).* | Standard; escalates to Strong only where `Rules.md` T4 or the high-risk rule requires it (§4.2) | 9 |
| **A5** | Growth, Pricing & Offer Intelligence | Offer architecture, price framing, incentive design | Standard | 9 |
| **A6** | Creative Campaign Intelligence | Copy, headlines, hooks, scripts, storyboards, poster prompts | Standard + Media | 6 |
| **A7** | Trust, Risk & Campaign Evaluation | Claim verification, risk flags, brand-safety score | Standard; escalates to Strong only where `Rules.md` T4 or the high-risk rule requires it (§4.2) | 6 |

**Adaptive selection.** A1, A6 and A7 always run — they are the floor (know the product, make the thing, check the thing). A2–A5 are selected by request type, sector risk and memory coverage. A run therefore uses **3 to 7 agents**, and the selection rationale is persisted for the evaluation.

**Failure policy.** An agent failure is recorded in `errors` and the run continues with a declared gap. One failing agent must never abort a campaign.

---

## 8. Validation engine

Three independent gates. Any `block` stops publication.

| Gate | Checks | Outcome |
|---|---|---|
| **Claim** | Every factual assertion maps to a `verified` claim or a scored source. **Registry:** a claim matching a `prohibited_phrase` rule for the brand's sector is blocked even if `verified` — a manufacturer stating something does not make it permissible to advertise. A claim matching a `required_disclaimer` rule's trigger must carry that disclaimer. | `pass` / `flag` / `block` |
| **Brand voice** | Cosine distance between generated copy embedding and brand voice vector. **Registry:** required disclaimer text is excluded from the similarity score and must appear verbatim; any rewording of it for voice is a block. | `pass` if above threshold; `block` on altered disclaimer |
| **Risk** | Restricted claims, regulated-sector language, superlatives without proof, 8 predatory marketing anti-patterns (**specified, not implemented** — no detector exists; `Rules.md` T5 is unmet; §8.3, `Phases.md` Phase 3). **Registry:** full copy scanned for every active `prohibited_phrase`; every applicable `required_disclaimer` must be present. | `pass` / `block` |

### 8.1 Sector constraint registry

The deterministic half of regulated-sector safety. It replaces A4 for required disclaimers and
prohibited phrasings (`Phases.md` §6.1; `Rules.md` T4). Table: `sector_constraints` (§9).

| Property | Rule |
|---|---|
| Content | Per sector and jurisdiction: `required_disclaimer` (text that must appear) and `prohibited_phrase` (wording that must not). Each row cites the regulation or guidance it comes from. |
| Authorship | Humans only. Rows are written by platform admins through a reviewed path; no agent or LLM output ever writes a row. An LLM may *suggest* a candidate to a human; the suggestion is not a rule until a human enters it. |
| Matching | Deterministic: case-insensitive phrase match or a stored regular expression. Never an LLM judgement. |
| Scope | Platform-wide reference data, not tenant data — the same rules apply to every organisation in a sector. Readable by all authenticated users; writable only by admins. |
| Fail closed | A brand whose sector is marked `regulated` but has no active registry rows **blocks the run**. Missing rules are never read as "no constraints". |
| Sector keys | `brands.sector` references a `sectors` table, so a misspelled sector cannot silently bypass the registry. |
| Change control | Rows are versioned by `effective_from` / `retired_at`, never edited in place (trigger); every change is written to `audit_log`. Binds the application roles, not the table owner (§9, stated limitation). |
| Provenance (0021, 2026-10-17) | Every sector states where its rules come from: `sectors.rules_provenance` is `authoritative` (cited from regulation) or `illustrative` (synthetic). Default `illustrative`, backend-settable only. A synthetic rule is marked by its source alone — `source_url` null and `source_note` exactly "Synthetic rule, not derived from any regulation. Illustrative only." — nothing citation-like; a cited rule needs a URL and a human author and may not use that sentence. A synthetic rule can only belong to an illustrative sector, and a sector with active synthetic rules cannot be marked authoritative (triggers). **Every report that names a sector prints its provenance** (`Sector.describe()`, the API's `Sector.rules_provenance`, the UI label), so an illustrative rule is never mistaken for a regulatory one. |

**What the research claims about the registry — and what it does not.** The claim is that the
mechanism enforces whatever rules it is given: deterministically, fail-closed, verbatim for
disclaimers. It is **not** that the rules shipped are legally correct. The one regulated sector,
health supplements, ships with **four synthetic rules** (0021: two prohibited phrasings, a phrase
and a regex; two required disclaimers, one on all output and one triggered by "protein"), written
to exercise every path of the mechanism — never derived from FSSAI, ASCI or any other source.
Substituting a real, cited rule set is a **deployment step, outside the research contribution**:
retire the synthetic rows, enter cited ones through the authoring path, then mark the sector
`authoritative` (the trigger refuses this while synthetic rules are active).

The anti-pattern blacklist (from AMAD-MS gap G6): false scarcity, fabricated social proof, undisclosed paid endorsement, vulnerability targeting, dark-pattern consent, unsubstantiated health claims, price anchoring on fictional prices, confirmshaming. **Status (2026-10-18): specified, not implemented.** No detector for any of the eight exists, none appears in the gates' outcome table, and `Rules.md` T5 is unmet until one does. Until then the only check is the human approver's (`docs/approval-rubric.md` S2.1; threat V3 in `docs/evaluation-plan.md`).

### 8.2 Product truth store and run admission — as built (Phase 3 chunk A, 2026-10-17)

**What the three claim types mean, as constraints a test asserts** (migration 0017;
`backend/tests/db/test_product_claims.py`). Stated at the database because members can write
`product_claims` through the REST API directly:

| Type | Database rule | Meaning |
|---|---|---|
| `verified` | `created_by = 'human'` (so `entered_by` is a user) **and** `evidence_url ~ '^https?://\S+$'` | A fact a person entered with a source a reader can open. No agent row is ever verified. |
| `deduced` | `confidence is not null` **and** (an evidence URL **or** a non-blank note) | An inference with its confidence and what it rests on. Shown as a deduction, never as fact (`Rules.md` P9). |
| `restricted` | a non-blank `evidence_note` (the reason) | Must never reach generated copy (`Rules.md` T2). May carry the URL that states it: a manufacturer saying it does not make it sayable. |

Who entered a claim, and who retired it and when, are written by the trigger from the JWT, never
from the request (§9, "Trust-deciding columns"). Claims are never edited or deleted: a wrong claim
is retired, once, with a reason, so a campaign that cited it keeps a true source trail.

**Memory eligibility (`Memory.md` §6.1, condition 2).** A claim may back a memory item only if it
is `verified`, or `deduced` with a *scored* source. Source scoring arrives in Phase 5, so until then
no deduced claim is eligible — the gate fails closed, it does not treat "no score" as a pass.

**Run admission (steps 2–3; `backend/app/core/admission.py`).** A run is admitted only if the
product has an active verified claim, the brand's sector is covered by the registry (no sector
blocks; a regulated sector with no active rules blocks — fail closed), and the brief asks for no
restricted claim of the product and uses no wording a registry rule for the sector prohibits.
Every failing check is reported together. **How it fails: blocked, never warned** (`Rules.md` T2):
the API answers `409` with every reason, nothing is queued, and an `audit_log` row
(`action = 'run.blocked'`, actor `service`/`run_admission`, the requesting user and the reasons in
`details`) is appended by the backend's own role. A failure to write that row is logged as an error
and the run stays blocked. Wired into the fake-run endpoint from Phase 3; Phase 6's
`POST /api/v1/runs` must insert the runs row first, call the same functions, and end a refused run
with status `blocked` (0015), so every gate-blocked run is counted per arm
(`backend/app/evaluation/run_outcomes.py`).

**Matching is deterministic and literal** (`backend/app/core/text_match.py`): case- and
punctuation-insensitive whole-word phrase match, or a stored regex; never an LLM judgement. **It
cannot catch a paraphrase of a restricted claim.** That gap is threat V2
(`docs/evaluation-plan.md`). `docs/approval-rubric.md` T1.3 ("in any form, including
paraphrase") is unchanged and correct: it tells the human approver what to look for, and a human
can catch a paraphrase. It is not a claim about what the code detects; until the classifier
(chunk C) is measured, the approver is what covers paraphrase.

### 8.3 Output gates and the authoring path — as built (Phase 3 chunk B, 2026-10-17)

**The gates** (`backend/app/core/gates.py`) judge generated copy against the product's truth and
the sector's active rules, deterministically:

| Gate | Checks | Findings |
|---|---|---|
| Claim | Each factual statement (an exact quote from the copy, with what it cites) is labelled from its citation, never by a model | resolves to a source (verified); `deduced_labelled` (flag); `unrepeated_quantity` (flag: the cited claim states a quantity the statement does not repeat — a resolved citation does not show entailment, `docs/evaluation-plan.md` V4; `backend/app/core/quantities.py`); `unsourced_claim`, `cites_unknown_claim`, `cites_retired_claim`, `cites_restricted_claim`, `deduced_unlabelled`, `statement_not_in_copy` (block) |
| Risk | The whole copy | `restricted_claim_in_copy`, `prohibited_phrase`, `missing_disclaimer`, superlatives in two tiers (Rules.md T3; a superlative passes only in a statement citing a verified claim whose own text states it): unambiguous ones unsupported → `unsupported_superlative` (block); context-dependent ones ("leading", "the only", "ultimate") unsupported → `context_superlative` (**flag** for the reviewer — raised for a person, neither silently permitted nor wrongly blocked), `registry_coverage`, `registry_rule_invalid`. **Not the eight anti-patterns (T5)**: no detector exists, so none is in the outcome table — T5 is unmet (`Phases.md` Phase 3) |
| Brand voice, registry half | Required disclaimers | `altered_disclaimer` — present but with case or punctuation changed, distinct from `missing_disclaimer`; `voice_scoring_text` strips disclaimer text before Phase 6's similarity, which is not built here |

`SEVERITY` is closed and immutable: every finding kind blocks except `deduced_labelled`,
`context_superlative` and `unrepeated_quantity`, which flag; an exception anywhere is a `gate_error` block. A blocked report
is appended to `audit_log` (`output.blocked`, actor `validation_engine`) and names the sector with
its provenance and the superlative list version (`config/superlatives/`: both tiers in one file
under one hash, so the thesis can state which word sat on which tier). **v1 was decided by the
project owner word by word on 2026-10-18, before any evaluation used it: 29 block, 11 flag**, with
"best" excepted before "before". Families ("most X", "highest X") are enumerated, never patterns.
Evidence and safety claims ("clinically proven", "doctor recommended", "100% safe", …) are kept
out on purpose: they belong to the claim gate, and a phrase in two gates would be counted twice.

**Matching contract** (`backend/app/core/text_match.py`, 2026-10-18): whole words, case- and
punctuation-insensitive; each gap between a phrase's words may be a space, a hyphen or dash, or
nothing ("market leading" = "market-leading" = "marketleading"); "#1", "Nr. 1", "No.1" read as
"no 1"; declared exceptions honoured. It applies to every phrase match — superlatives, the
product's restricted claims and registry phrase rules — not to required disclaimers, which use the
verbatim rule.

**The authoring path** (`backend/scripts/apply_sector_constraints.py`, `config/sector_constraints/`):
reviewed YAML files, dry run by default, committed files only (the commit recorded in every audit
row), a named human author, append and retire only — an active rule missing from its file stops the
run — cited rules only, parameterised inserts, every change in `audit_log`.

**Write-back is staged** (`backend/app/core/budgeted_run.py`): steps append memory writes to an
in-process stage; it is committed in the transaction that writes the terminal status, only for a
completed run, and discarded otherwise — so no write reaches the undeletable `memory_items` before
the attempt's primacy is known (`docs/evaluation-plan.md` §2).

**The claim classifier** (Phase 3 chunk C, 2026-10-18; `backend/app/core/claim_classifier.py`,
prompt `claim_extract@v1`): a model extracts every factual statement in the copy as an exact
quote and names the listed claim it rests on, or none. It never labels anything — the claim gate
computes the label from the cited claim's stored type — and every quote is checked against the
copy. The claims a model sees (`truth_context`, also the view Phase 6's copy writer will get) are
active verified and deduced only: **a restricted claim is never shown to a model** (Rules.md T2,
input side). Regulated-sector output is classified on the strong tier (T4); with the strong tier
off, the copy is blocked, not downgraded. Any model failure blocks the copy (`gate_error`).
`validate_output` appends every blocked report to `audit_log`. Proven with a mocked model only:
how well a real model extracts — and in particular whether it cites a verified claim for a
paraphrase of a restricted one — is **not measured** (threat V2).

**What Phase 3 cannot show without Phase 6:** there is no generator yet, so no real output has
passed through the gates. Every output-side criterion rests on hand-written sample copy and
scripted model replies, and stays provisional in `Phases.md` until real generated output does.

---

## 9. Data model

**`docs/erd.md` is canonical** — it records why each column and constraint exists (issues
E1–E13) and the role matrix. This section must match it; change both in the same commit. The DDL
is `supabase/migrations/`, listed with the commit that added each file at the end of this section;
the files are not copied here.

**Tenant consistency.** Every tenant table carries its own `org_id`. A child's `org_id` is forced
to equal its parent's by a composite foreign key `(parent_id, org_id) → parent (id, org_id)`, so a
row can never point across tenants. Composite `on delete set null (col)` requires
**PostgreSQL 15+** (`docs/erd.md`: confirm before the first migration).

**Role-aware RLS.** Every tenant policy checks the row's *own* `org_id` against `org_members`,
keyed on `auth.uid()`, always written `(select auth.uid())` so Postgres evaluates it once per
statement rather than once per row. Read policies use `org_id in (select app.member_org_ids())` —
the orgs where the caller holds any unrevoked role, likewise evaluated once per statement. Write policies that need a specific
role call `app.has_role(org_id, roles)`. Both are stable `security definer` functions. Policies
never walk parent tables; the membership lookup is the one lookup they make. Writes the backend
performs (`llm_calls`, `memory_items` inserts, campaign status and approval, approval decisions,
`audit_log`) use the service role and are constrained by keys, checks and triggers rather than
by RLS.

**No organisation in the JWT (E12).** A user may belong to several organisations — the agency
persona (`PRD.md` §3) and any consultant across client orgs — so the JWT carries no `org_id`
claim and **no Supabase custom access token hook is built**. RLS admits a user to every org they
are a member of; which org a request is *for* is an explicit parameter on the API (`org_id` in
the path or body, checked against the caller's memberships), and every tenant query still filters
on it (`Rules.md` P3). The selected org in the UI is a convenience, not a security boundary.

**Approval (Rules.md T6).** `campaigns.approved_by` must be a member of the *same* organisation
holding the `approver` role — enforced by a foreign key on `(org_id, approved_by, 'approver')` —
and that role must be unrevoked at the time of approval (trigger). An approved campaign must
name its approver.

**Approval decisions (E13).** Every decision the approver makes — approve, revise, reject or
undecided — is a row in `approvals` (`docs/approval-rubric.md` §5): campaign, org, approver,
outcome, failed gate, reason, seconds spent, arm inference and its note, decision time. The
approver is constrained exactly as `campaigns.approved_by` is (role FK + active-approver trigger);
a reason is required on anything but `approve`; direct `update` and `delete` are revoked from
every application role, so a re-score after a rubric amendment is a new row (limit: the table
owner — see the stated limitation under "Audit log" below). `campaigns.approved_by` stays the operational
approval state; `campaigns.quality_scores` holds only the blind raters' scores and never the
approver's decision. Calibration decisions (`docs/approval-rubric.md` §6) are rows here too, made
in a practice organisation where the second scorer holds `approver`, and flagged
`is_calibration`. They are never deleted (within the limits stated under "Audit log" below); the evaluation harness excludes them by that flag
(`backend/app/evaluation/approvals.py`), as it excludes seed memory (`Memory.md` §7.1) — never by
the practice org's id, which changes on a reseed. **The practice organisation is a row in
`organisations`, not a second Supabase project**, so it does not use one of the free tier's two
active projects (§11). The practice org is itself marked by a flag,
`organisations.is_practice` (backend-set; owners cannot update it), and a trigger rejects any
approval in a practice org without `is_calibration = true`, so an unflagged practice row cannot
exist. The harness additionally excludes practice orgs, so it could not leak even if the trigger
were bypassed.

**Trust-deciding columns — the general rule (adopted 2026-10-17).** *Any column that decides a
trust level or a reported result is derived from the authenticated identity or by the server,
never supplied by the caller.* The API is not the boundary: members reach every table the RLS
policies open to them through Supabase's REST API directly, with whatever column values they
choose, so a check that lives only in our API is decorative. The rule has come up three times,
each time with the same fix:

| Instance | Caller-supplied value that would have decided trust | Derived instead from |
|---|---|---|
| E12 — the organisation in the JWT | an `org_id` claim chose which tenant's rows a user saw | `org_members`, keyed on `auth.uid()` |
| The run's organisation (Phase 1, B2) | a fake run's org taken from the request | the `run.queued` event the API writes after checking membership |
| `product_claims` provenance (0017) | `created_by = 'human'` made a claim eligible to be `verified` | `auth.uid()`, in a trigger: `created_by`, `entered_by`, `created_at`, `retired_by`, `retired_at` are overwritten for the API roles; the backend may not write `'human'` at all |

Applied in the same change to the other places it held: **`runs`** (0016) — an owner or editor may
insert a run, but its status, counts, cost and timestamps are forced to the server's initial
values, so a run cannot arrive already `blocked`, `completed` or costed and be counted in Phase 10;
**`brands.sector`** (0019) — the sector decides which registry rules bind a brand, but only the
brand's owner knows it, so it cannot be derived; the caller supplies it and every set or change is
written to `audit_log` by a security-definer trigger, with the actor read from the JWT.

Instances left open in Phase 3 (scope, `Rules.md` §1.3), and their state:
- ~~`memory_items.revoked_at` is supplied by the revoking user~~ — **closed by 0023 (Phase 4
  chunk A, 2026-10-18)**: a trigger sets `revoked_at = now()` and, for a signed-in user,
  `revoked_by = auth.uid()`; revocation is one-way and recorded once.
- ~~`memory_items.claim_id` is `on delete set null`~~ — **closed by 0023**: the foreign key is now
  `no action`, so a product whose claims back a memory item cannot be deleted.
- `runs.arm` is chosen by whoever inserts the run — open; closed by the experiment-membership
  design (below), which forces it with the other experiment markers (Phase 10).

A new table or column is checked against this rule when it is added: if its value changes what
the system trusts or reports, it is derived, or its setting is audited with a derived actor.

**Experiment membership — designed 2026-10-17, approved, not built (Phase 10).** The evaluation
reads built so far take a list of organisation ids and subtract the `is_practice` / `is_test`
flags, then scan every run in those organisations: a stray manual run, a re-run or a debugging run
inside an evaluation organisation would be counted, and the organisation list is a set of
remembered ids. Membership becomes **opt-in**, so that forgetting a marker excludes data instead of
including it:

| Element | Design |
|---|---|
| `experiments` | One row per experiment: name, the frozen definition (arms, request set and its file hash, prompt versions lock hash, model registry hash, approver, attempt rule as pre-registered in `docs/evaluation-plan.md` §2), created by the harness role. **Immutable once any run references it**: a trigger refuses update and delete of an experiment row while a `runs` row points at it, so its definition cannot change mid-evaluation and runs cannot be silently re-pointed — the append-only reasoning of `approvals`. |
| `runs.experiment_id`, `runs.arm`, `runs.request_ref`, `runs.attempt` | **Set together by the harness's own database role, or not at all** (a CHECK: all four null, or all four non-null). `arm` becomes nullable for this — null means "not an experiment run"; today it is `not null default 'proposed'`, which would label every stray run as Proposed. A run inserted by an API role gets all four forced to null by the 0016 trigger's successor, as status already is: a user can neither enter a run into the experiment nor relabel its arm, which would corrupt the comparison more quietly than an extra run. No API role can update them (no update policy on `runs`, 0005); the harness role cannot change them after insert either (trigger: the four are written once). |
| Uniqueness and the primary attempt | `unique (experiment_id, request_ref, arm, attempt)`; `attempt` starts at 1 and is assigned by the harness in start order. Re-runs are explicit rows, never replacements. `runs.infrastructure_failure` (null, or one of `rate_limited`, `network_error`, `process_crash`, `quota_exhausted`) is set by the harness role from the exception that ended the attempt, never by a person, and written once. The primary result is pre-registered (`docs/evaluation-plan.md` §2, fixed 2026-10-17): the first attempt that terminated without infrastructure failure — a `blocked`, `partial` or poor result is a result. Only the primary attempt may write back to memory. |
| Second defence | A marked run in an `is_test` or `is_practice` organisation is refused (trigger), and every read keeps both exclusion filters. |
| Reads | Every evaluation read (`backend/app/evaluation/`) selects by `experiment_id`, never by an organisation list; the structural test that every `*_SQL` read carries the exclusion filters is extended to require the experiment filter. |

**Audit log.** Append-only **for the application roles only**: `update`, `delete` and `truncate`
are revoked from `anon`, `authenticated` and `service_role`, and a trigger rejects them.

> ### Stated limitation — what "append-only" and "never deleted" mean on managed Supabase
> These protections bind the **application roles** (`anon`, `authenticated`, `service_role`),
> which are all the API and backend use. They do **not** bind the table owner. On Supabase every
> table is owned by `postgres` — the role the dashboard SQL editor uses, and the role migrations
> run as — and `postgres` can `alter table audit_log disable trigger …` and then update or delete
> rows (verified on the project 2026-10-14, in a rolled-back transaction). The trigger stops an
> owner's *accident*, not deliberate tampering. Supabase gives this project no role above
> `postgres`, so **no in-database protection against the owner is possible**: whatever `postgres`
> installs, `postgres` can remove.
>
> State on the project after migration 0011 (checked 2026-10-14, 39 of the 40 checks in
> `backend/tests/db/test_table_posture.py` — the table delete/truncate checks; see "What a posture
> count covers" in §11 — matching):
>
> | Table | Claim | Holds against the application roles | Holds against `postgres` |
> |---|---|---|---|
> | `audit_log` | append-only | Yes (grants revoked; trigger also blocks `truncate`) | No |
> | `approvals` | append-only | Yes: direct `update`/`delete`/`truncate` revoked, and no application role can delete the `runs` or `organisations` rows that would cascade into it (0010) | No |
> | `memory_items` | never hard-deleted (`Memory.md` §6.4) | Yes: no direct delete or truncate (0010), and a brand that has memory items cannot be deleted (0011) | No |
> | `org_members` | roles revoked, never deleted | Yes (0010) | No |
> | `sector_constraints` | never edited in place or deleted | Yes: `update`/`delete` blocked by trigger, `delete`/`truncate` revoked (0011) | No |
> | `llm_calls`, `runs` | raw logs, retained (`Rules.md` A1) | Yes (0010, 0011) | No |
> | `product_claims` | never edited or deleted; retired once, by a signed-in user (added 2026-10-17) | Yes: trigger allows only retirement, `delete` revoked (0017); deleting a product with no runs still cascades | No |
> | `runs.status` | set by the backend only (added 2026-10-17) | Yes: no update policy (0005), user inserts forced to `queued` (0016) | No |
>
> **No application role holds `TRUNCATE` on any table** (0011); `TRUNCATE` ignores row-level
> security, and Supabase's default privileges grant it on every new table, so a new table must
> revoke it. The class is closed by a test, not by vigilance: `test_table_posture.py` states the
> expected effective `delete` and `truncate` of **every** table for `anon`, `authenticated` and
> `service_role`, and fails on any table not listed (merge-blocking, `Rules.md` §6).
>
> **Deliberate consequence of 0011 — accepted 2026-10-14.** `memory_items.brand_id` is now
> `on delete no action`, so **an organisation owner can no longer delete a brand once it has
> memory items**, as they already cannot delete a product once it has runs. This is a deliberate
> trade of convenience for evidence integrity, not an oversight: deleting a brand would otherwise
> hard-delete the memory the evaluation measures. Deleting the whole organisation, by the manual
> `postgres` procedure below, still removes everything.

> **Decisions on hardening (2026-10-14):**
> 1. **Revoking application-role deletes — done, as a correctness fix, not hardening:**
>    migrations 0010 and 0011. The application role deleting rows the design keeps was a violation of the
>    documented design (`Memory.md` §6.4, approvals' append-only property), not a platform limit.
> 2. **Hash chain — approved in principle, deferred, scoped to `approvals` and `audit_log`.** A
>    before-insert trigger stores `sha256(previous hash ‖ row)` (`pgcrypto` `digest()` is
>    available), inserts serialised by an advisory lock; the head hash is anchored outside the
>    database, committed to git with the weekly `pg_dump`. **Built and verified before
>    calibration begins** (`Phases.md` Phase 10). Its value is evidential: `Rules.md` A1 requires
>    every reported number to trace to a raw log, and the chain is how the project shows that
>    approvals and audit events were not edited after the fact. It is **detection, not
>    prevention**, and is claimed as nothing more.
> 3. **Diffing successive weekly dumps — declined** as redundant with the hash chain: the chain
>    detects any altered or deleted row, including rows written between two dumps, which dump
>    diffing cannot see.
> 4. *Not available:* transferring ownership to a role `postgres` cannot use (PostgreSQL requires
>    the current owner to be able to `SET ROLE` to the new owner — denied on the project), and an
>    event trigger blocking DDL (allowed, but owned by `postgres`, which can drop it; it only adds
>    a step).
>
> **Stale comments in migrations.** Migrations 0002 (`sector_constraints rows are retired, never
> deleted`) and 0003 (`Revoked, never deleted`) carry comments written before this block. They
> state a guarantee that does not hold against the table owner, and they **cannot be edited** under
> the `Rules.md` §3.3 trigger. A reader finding them should read this section instead.

**Deleting an organisation — manual procedure.** Since 0010 no application role can delete an
organisation; it is a deliberate operation by `postgres`, the table owner:

1. **Never while its runs, campaigns or approvals are evaluation evidence** (`Rules.md` A1): the
   cascade removes them. Archive first, or do not delete.
2. Take a `pg_dump` of the project and keep it.
3. Append an `audit_log` row recording who is deleting which organisation, and why
   (`actor_type = 'user'`, `action = 'delete_organisation'`).
4. In the SQL editor (or `psql` as `postgres` through the session pooler), in one transaction:
   `delete from organisations where id = '<org id>';` — the foreign keys cascade to its members,
   brands, products, claims, runs, LLM calls, memory, campaigns and approvals. `audit_log` rows are
   kept: they carry no foreign key.
5. Confirm the expected rows are gone, then commit. A DPDP erasure request follows the separate
   procedure in `PRD.md` (pending legal review), not this one.

**Organisation budget and rate limits.** Checked alongside the per-run cap (`PRD.md` §9), never
instead of it:

| Limit | Column | Enforced |
|---|---|---|
| Monthly spend | `organisations.monthly_budget_cap_usd` | Pre-flight, before every LLM call: the estimated cost must fit within **both** the run's remaining budget and the organisation's remaining month-to-date budget (`sum(llm_calls.cost_usd)` for the org in the current UTC calendar month). A breach yields `budget_exhausted`, as for the run cap. **Built 2026-10-17** in the ledger's admission (`app/llm/ledger.py`, §4.4); not enforced before that date. |
| Run starts | `organisations.rate_limit_runs_per_hour` | At `POST /api/v1/runs`, Redis counter per org; over the limit → `429`. |
| API requests | `organisations.rate_limit_requests_per_minute` | At the API gateway, Redis counter per org; over the limit → `429`. |

The column defaults (50.00 USD, 10 runs/hour, 60 requests/minute) are **placeholders, not derived
values**; set them per organisation and report any values used in evaluation.

**Migrations.** The DDL is the migration files themselves; this section does not copy them (keeping
a copy in step needed a syncing script — removed 2026-10-18). Each row gives the file and the commit that added it; "Changed" is set only where a
file was edited after it was added, which `Rules.md` §3.3 allows only before a migration first
reaches the project. A test (`backend/tests/test_schema_doc_sync.py`) fails if a migration file
is missing from this table, listed twice, out of order, or listed but absent.

| Migration | Added | Changed | What it does |
|---|---|---|---|
| `supabase/migrations/0001_extensions.sql` | `cd53e98` | `c67416e` | `vector`, `pg_trgm`, the `app` helper schema |
| `supabase/migrations/0002_sectors_and_registry.sql` | `cd53e98` | | Sectors and the versioned sector-constraint registry (§8.1) |
| `supabase/migrations/0003_tenancy_and_rbac.sql` | `cd53e98` | `c67416e` | Organisations, membership roles, `app.member_org_ids()`, `app.has_role()` |
| `supabase/migrations/0004_product_truth.sql` | `cd53e98` | `c67416e` | Brands, products, the product truth store |
| `supabase/migrations/0005_runs_and_llm_calls.sql` | `cd53e98` | `c67416e` | Runs and the per-call LLM log |
| `supabase/migrations/0006_memory_items.sql` | `cd53e98` | `a5578ce` | Marketing memory, HNSW and GIN indexes (`Memory.md`) |
| `supabase/migrations/0007_campaigns.sql` | `cd53e98` | `c67416e` | Campaigns and the approval constraint (T6) |
| `supabase/migrations/0008_audit_log.sql` | `cd53e98` | | Append-only audit log |
| `supabase/migrations/0009_approvals.sql` | `c67416e` | `7266cec` | Approval decisions (`docs/approval-rubric.md` §5) |
| `supabase/migrations/0010_revoke_app_role_deletes.sql` | `823951e` | | Application roles cannot delete never-deleted rows |
| `supabase/migrations/0011_close_remaining_delete_paths.sql` | `b0d7e37` | | Completes 0010: the remaining delete and truncate paths |
| `supabase/migrations/0012_create_organisation.sql` | `f8cb85f` | | `app.create_organisation`: a signed-in user creates an org and owns it |
| `supabase/migrations/0013_llm_call_waits_and_outcome.sql` | `d8905fb` | | `llm_calls` records rate-limit waits and each call's outcome |
| `supabase/migrations/0014_llm_call_prompt_version.sql` | `454596f` | | `llm_calls` records the prompt template and version |
| `supabase/migrations/0015_runs_status_check.sql` | `9706e48` | | The run statuses, as a CHECK |
| `supabase/migrations/0016_runs_initial_state_from_server.sql` | `9706e48` | | A user-inserted run starts in the server's initial state |
| `supabase/migrations/0017_product_claims_truth.sql` | `9706e48` | | Claim definitions as constraints; provenance derived by the server |
| `supabase/migrations/0018_seed_sectors.sql` | `9706e48` | | The nine sector keys |
| `supabase/migrations/0019_audit_brand_sector.sql` | `9706e48` | | Every brand sector change audited |
| `supabase/migrations/0020_organisations_is_test.sql` | `b694beb` | | Test organisations flagged; evaluation reads exclude them |
| `supabase/migrations/0021_sector_rules_provenance.sql` | `fb5f2cf` | | Rule provenance per sector; the synthetic regulated-sector rules |
| `supabase/migrations/0022_match_memory.sql` | `af4dd3a` | `771eba0` | `app.match_memory`, hybrid retrieval fused by RRF (`Memory.md` §4.2) |
| `supabase/migrations/0023_memory_trust.sql` | `af4dd3a` | | Revocation recorded by the server and one-way; claim-backed memory keeps its claim |
| `supabase/migrations/0024_memory_decay_revocation.sql` | `bd8be17` | | A revocation by decay recorded as the system's act: `revoked_by` null iff a constrained decay record (`Memory.md` §6.4) |
| `supabase/migrations/0025_memory_confidence_required.sql` | `3c3c869` | | `memory_items.confidence` NOT NULL, no default: no item can be exempt from decay (`Memory.md` §6.3) |
| `supabase/migrations/0026_memory_confidence_bounds.sql` | `219d5c7` | | Confidence within [0, 1]; a live item never below the revocation threshold (`Memory.md` §6.3) |
| `supabase/migrations/0027_memory_decay_record_nonnegative.sql` | `219d5c7` | | The decay record pattern drops the negative confidence (`Memory.md` §6.4) |

A new migration adds its row here in the same commit, by direct edit.

---

## 10. API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs` | Start a campaign run → `202` + `run_id` |
| `GET` | `/api/v1/runs/{id}` | Status, current step, budget consumed |
| `GET` | `/api/v1/runs/{id}/stream` | SSE step-by-step progress for the live pipeline UI. Signed-in members of the run's organisation only (`404` otherwise; §11, Authentication) |
| `POST` | `/api/v1/runs/fake` | Phase 1: start a fake 15-step run (no LLM, no pipeline) → `202` + `run_id`; never mounted in production. From Phase 3 it names a `product_id` and may carry a `brief`, and is admitted first (§8.2): refused → `409` with every reason, audit-logged |
| `GET` | `/health`, `/health/ready` | Liveness (no dependencies); readiness (Redis and database, `503` if either fails) |
| `POST` | `/api/v1/runs/{id}/approve` | Resume the interrupted graph |
| `POST` | `/api/v1/runs/{id}/revise` | Request revision (bounded by `max_revision_rounds`) |
| `GET` | `/api/v1/campaigns/{id}` | Final package with evidence |
| `GET` | `/api/v1/me` | The signed-in caller and their organisations, with roles |
| `POST` | `/api/v1/organisations` | Create an organisation; the caller becomes owner (`app.create_organisation`, 0012) |
| `CRUD` | `/api/v1/brands`, `/products`, `/products/{id}/claims` | Setup. Built in Phase 1: list, create and delete brands and products, each with an explicit `org_id`. Phase 3 (2026-10-17): brands carry a `sector`; claims are listed (`GET`), entered (`POST`) and retired (`POST /products/{id}/claims/{claim_id}/retire`), never edited or deleted (§8.2) |
| `GET` | `/api/v1/sectors` | The registry's sector keys, with `regulated` (Phase 3) |
| `PATCH` | `/api/v1/brands/{id}/sector` | Set or change a brand's sector; every change audited (0019) (Phase 3) |
| `GET` | `/api/v1/memory/search` | Debug and demo view of retrieval with `M` breakdown |
| `POST` | `/api/v1/experiments/run` | Execute an evaluation arm |
| `GET` | `/api/v1/experiments/report` | ARR, SRR, CR, LR |

The SSE stream is what makes the 15-step pipeline visible in the demo. **Build it in Phase 1, not Phase 11** — it is the single highest-value demo asset in the project.

---

## 11. Deployment

| Component | Host | Notes |
|---|---|---|
| Frontend | Vercel (free) | Preview deploy per PR; the public URL serves a seeded, read-only demo path |
| API + worker | **Developer's laptop** | Decided 2026-10-14; see "The laptop is the worker host" below |
| Postgres + pgvector | Supabase | Free tier (limits below) |
| Redis | **Developer's laptop** (Docker, AOF on) | Queue + zero-retention research cache; a hosted free tier cannot carry ARQ polling (below) |
| SearXNG | **Developer's laptop** (Docker) | Self-hosted and free; public instances rate-limit |
| Secrets | Platform env vars; a local `.env` on the laptop, git-ignored | Never in the repo. See `Rules.md`. |

**Zero-rupee constraint (2026-10-14).** Everything runs on a free tier; nothing is paid
(`Rules.md` §4, C0). Every host above is free for its role (checked 2026-10-14).

### Supabase free tier — limits designed within

Checked on supabase.com/pricing and the platform docs, 2026-10-14:

| Limit | Free tier | Effect on this design |
|---|---|---|
| Database size | 500 MB per project | Storage budget below. Supabase's own schemas count against it: measure `pg_database_size(current_database())` on the empty project at creation. |
| Active projects | 2 | One project holds every organisation, evaluation and practice alike: organisations are **rows**, never projects. Tests and CI use a local container, not a project. |
| Inactivity | Paused after 1 week | See `Phases.md` Phase 11 and the risk checkpoints: touch the database at least weekly. |
| Egress | 5 GB / month | Retrieval returns ≤ 12 items per query; the harness reads logs, not embeddings. Well within it. |
| Compute | Shared CPU, 500 MB RAM | One small instance: connect through the Supavisor pooler, keep worker concurrency low, and expect HNSW builds and evaluation batches to be slow, not failing. |
| Backups | Not included | Take our own: `pg_dump` to local disk weekly, and after every evaluation batch (the runs are `Rules.md` A1 raw logs). |

Nothing in `supabase/migrations/` assumes more: the extensions (`vector`, `pg_trgm`) are available
on the free tier, there are no partitions, replicas, cron jobs or background workers in the
database, and the per-row sizes measured on 2026-10-14 put `memory_items` far below 500 MB for
the full evaluation. The large unknowns are outside the current schema: LangGraph checkpoint rows
(Phase 6) and any persisted research-finding embeddings (Phase 5); both must be measured before
the evaluation.

**Restoring a paused project.** Open the project in the Supabase dashboard and press *Restore
project*; it restores in place with the same URL and keys, moved to the latest minor Postgres
version. The one-click restore window is stated as 1 year in the docs text but shown as 90 days in
their screenshot — plan on 90 days. After the window, download the backup from the dashboard and
restore it into a new project (or locally), then update `SUPABASE_URL` and keys everywhere.

### The laptop is the worker host — deployment decision (2026-10-14)

Render's free tier has no background workers (render.com/docs/free, checked 2026-10-14), and
SearXNG is self-hosted on the developer's laptop. These are one decision: **the laptop is the
worker host.** The API, the ARQ worker, Redis and SearXNG run on the laptop (Docker); Supabase and
the Gemini API are reached from it over the internet. **Development and all evaluation runs
execute on the laptop.**

- **Phase 5's design works unchanged.** The worker reaches SearXNG only through its JSON API at
  `SEARXNG_URL` behind the search interface, so the host is configuration (`localhost` here).
- **The laptop must be on** for any live run. When SearXNG is unreachable, research degrades to a
  partial result, never a crash (Phase 5 exit criteria). Search results are cached by normalised
  query (`Rules.md` C7).
- **Redis is local too.** A hosted free Redis cannot carry the queue (below), and a laptop worker
  needs a reachable Redis anyway. Enable AOF persistence so queued jobs survive a laptop restart.
- **The public URL cannot run the live pipeline.** Nothing on a zero-cost cloud host can reach the
  laptop's services without exposing them. The public deployment is the frontend on Vercel with a
  seeded, read-only demo path; the live pipeline is demonstrated from the laptop. See `Phases.md`
  Phase 11 for the amended exit criterion.

**Verified, not assumed (2026-10-14).** On the laptop (Windows 11, 12 CPUs, 8 GB RAM visible to
Docker): Docker Desktop started and `docker info` answered (server 29.7.2). `redis:7.4-alpine`
(Redis 7.4.11) ran with `--appendonly yes`, answered `PING` with `PONG`, and reported
`appendonly yes`. Docker was used; the WSL2 Ubuntu + `redis-server` fallback was not needed. Docker
Desktop must be running whenever the worker runs. **SearXNG in Docker is not yet verified** — its
JSON API is checked in Phase 5.

### Redis free tier — checked, and why Redis runs locally

Upstash's free plan (upstash.com/pricing/redis, checked 2026-10-14): **250 MB data, 500K commands
per month**, 10 GB bandwidth, 10 MB max request. Against this design:

| Load | Estimate | Against the free plan |
|---|---|---|
| Chunk embeddings for one deep-research run, held with a TTL | about 8 MB (20 queries × 5 pages × 8 chunks × about 10 KB) | Fits 250 MB: TTL keys expire, so only runs inside the TTL window occupy space at once. |
| Chunk-cache commands | about 2 per chunk, about 1,600 per deep-research run | Fits on its own. |
| **ARQ queue polling** | ARQ 0.28's default `poll_delay` is 0.5 s: about 2 commands/s per idle worker, about **172,800/day** | **Breaches the plan: 500K commands would be gone in about 3 days.** |

So a hosted free Redis is not viable for the queue, and Redis runs on the laptop with the worker
(no command or storage cap beyond the laptop's RAM). Chunk embeddings stay in Redis with a TTL and
only findings that reach a campaign are persisted, per `Rules.md` P11 and `Memory.md` §6.2. If the
API is ever hosted separately, the queue must move with it and the polling figure above decides
the Redis plan.

### Supabase project — migrations applied; the `Rules.md` §3.3 trigger has fired

**2026-10-14 14:58:26 UTC: migrations 0001–0009 applied to the Supabase project**, as they stand
at commit `de4799e`, in **one transaction** (committed only after all nine succeeded), as the
`postgres` role through the session pooler. **This is the `Rules.md` §3.3 trigger event: from this
moment every schema change is a new migration — no in-place edits, no exceptions.**

| Item | Value (read 2026-10-14) |
|---|---|
| PostgreSQL | 17.11 (`docs/erd.md` §3 prerequisite: PG 15+ — met) |
| `vector` | 0.8.2, installed in `public` (CI's container has 0.8.7; the HNSW storage figures above were measured on 0.8.7) |
| `pg_trgm` | 1.6, in `public` |
| Database size | 10.8 MB empty (Supabase's own schemas), 11.7 MB after the migrations |
| Result | 13 tables, row-level security on every one, 27 policies, 7 `app.*` functions, 6 triggers |
| Migration history | 0001–0009 were applied with psycopg; the Supabase CLI was adopted the same day, before 0010. `supabase migration repair --status applied 0001 … 0009` recorded them in `supabase_migrations.schema_migrations`, and **0010 was applied with `supabase db push`** (2026-10-14 15:13 UTC). The CLI accepts the `NNNN_name.sql` convention (all ten listed, none skipped). |

**Decision (2026-10-14): `vector` and `pg_trgm` stay in `public`.** `create extension` puts them
in the first existing schema on the search path, which is `public` both on Supabase and in CI.
Supabase's convention is the `extensions` schema, and **its database linter will flag both as
"extension in public"** — an accepted warning, not drift. Reasons:

- **Same resolution in the gate and in production.** Supabase's search path includes
  `extensions`; CI's container does not. Moving the extensions would make every future migration
  and function either schema-qualify vector types and operators (`extensions.vector`,
  `operator(extensions.<=>)`) or behave differently in CI than on the project. Leaving them in
  `public` keeps one resolution everywhere.
- **The linter's risk is already contained.** Its concern is unqualified names resolving to
  objects in a schema the API roles can use. Every `app.*` function pins `search_path = ''`, and
  the API roles already have `USAGE` on `public`.
- **Not lock-in.** `alter extension … set schema extensions` was tested on the project
  (2026-10-14, rolled back) and is allowed; it changes catalog entries only, without rewriting
  tables or indexes, so its cost does not grow with data. Revisit only with a concrete reason,
  as a new migration.

**Known gap — pgvector version (2026-10-14).** The merge-blocking database gate runs pgvector
**0.8.7** (CI's `pgvector/pgvector:pg17` image); the Supabase project runs **0.8.2**. The gate
therefore tests a different vector implementation than production, and the HNSW storage figures
below were measured on 0.8.7. The memory subsystem is the thesis, so this is tracked, not
assumed away: Phase 4 re-verifies HNSW behaviour and storage against the real project once
`memory_items` holds data (`Phases.md` Phase 4). A `pgvector/pgvector:0.8.2-pg17` image exists
(Docker Hub, checked 2026-10-14), so CI can be pinned to production's version; Supabase may move
the project to a newer minor version on a restore (§11, restoring a paused project), so the two
are re-compared after any restore or upgrade.

**Known limitation — tests run as a superuser (recorded 2026-10-18).** CI and the local database
suite apply migrations and run every test as a superuser; on the project, migrations run as
`postgres`, which is not one. Anything a superuser may do that `postgres` may not passes the
merge-blocking gate and fails only on the live push. It has hidden two things so far: revoking
EXECUTE on a function `postgres` no longer owns (0012, below) and attaching an unloaded
extension's setting to a function (0022's first draft, below). The live dry run (below, `Rules.md` §3.3) is the current mitigation, not a fix; a
non-superuser migration role in CI is a Phase 11 concern.

**Platform compared with `backend/tests/db/supabase_platform_stub.sql`** (read-only, 2026-10-14):

| Aspect | Real platform | Stub | Effect on the RLS tests |
|---|---|---|---|
| `auth.uid()`, `auth.jwt()` | Bodies identical (whitespace aside); `stable`, not security definer; owner `supabase_auth_admin` | Same bodies and volatility; owner is the test superuser | None |
| `anon`, `authenticated` | nologin, no BYPASSRLS, INHERIT | nologin, no BYPASSRLS, NOINHERIT | None: neither is a member of any role, so INHERIT has nothing to inherit |
| `service_role` | BYPASSRLS, INHERIT, member of no role | BYPASSRLS, NOINHERIT | None |
| Schema owner / migration role | `postgres`: **not** superuser, BYPASSRLS, can `SET ROLE` to all three API roles | Superuser | None for the policy tests, which switch to the API roles; the migrations needed no superuser right (`vector` and `pg_trgm` are on Supabase's privileged-extension list; `postgres` has REFERENCES on `auth.users`) |
| Default privileges in `public` | Tables `arwdDxtm`, sequences `rwU`, functions `X` to `anon`, `authenticated`, `service_role` | `grant all` on tables, sequences, functions to the same roles | Same effective grants |
| `USAGE` on `public` and `auth` | All three API roles | All three API roles | None |
| `auth.users` | 35 columns | `id` only | Seeding only; no policy reads `auth.users` |
| Login role for the API | `authenticator` (member of the three API roles) | none; tests use `set local role` | None: PostgREST also switches roles with `SET ROLE` |
| Migration role's rights over objects it creates (added 2026-10-17) | `postgres` is not a superuser: once it hands a function to another owner it can no longer revoke that function's grants, and Supabase grants EXECUTE on new functions to the API roles | Superuser: it can revoke anything, from any owner | **Not modelled.** EXECUTE grants on new `app.*` functions can differ between CI and the project — see the lesson below |

Conclusion: in everything the RLS policies depend on — the two auth functions, the API roles'
RLS-relevant attributes and the default grants — the stub matches the platform, so the CI
database suite's policy results transfer. A separate suite against the live project was not built.

**Lesson (2026-10-16, migration 0012): the stub does not model function EXECUTE grants.** The
comparison above (2026-10-14) covered the auth functions, the roles' attributes and the default
grants — not who may EXECUTE a new `app.*` function. A rolled-back dry run of 0012 on the live
project found that **anon and service_role could still execute `app.create_organisation`**: the
migration revoked EXECUTE after handing the function to its new owner, and Supabase's `postgres`,
no longer owner, revoked nothing (silently). CI applies migrations as a superuser, which can
revoke anything, so the merge-blocking suite passed. The migration was corrected before it was
applied. **Rule: every migration that creates or changes an `app.*` function gets a rolled-back
dry run against the live project before it is applied** (below), because the stub cannot catch
this surface.

### Applying migrations — Supabase CLI (adopted 2026-10-14)

Every migration from 0010 on is applied with the Supabase CLI, pinned to **2.120.0** and run
through `npx` (no global install), against the session pooler string:

```
npx supabase@2.120.0 migration list --db-url "$DATABASE_URL"     # repo vs project, before and after
npx supabase@2.120.0 db push --dry-run --db-url "$DATABASE_URL"  # shows exactly what will apply
npx supabase@2.120.0 db push --db-url "$DATABASE_URL"
```

`supabase_migrations.schema_migrations` on the project is the record of what has been applied;
`migration list` must show local and remote identical after every push, and
`uv run python scripts/live_posture.py --url "$DATABASE_URL"` (read-only; below) must report every posture
check matching. **This check is manual on purpose, not by oversight:** running it from CI would put
the live project's credentials in a workflow, which `Rules.md` P16 forbids (no credentials for the
live project in CI, in any workflow, for any purpose): a live key in CI is reachable by every
workflow change and every unattended run on every push, and the live project is the single record
of the schema and, later, the data. Do not add this check to CI, and do not give CI a key to do so.
It is run from the laptop after each live migration and periodically. The full database suite
passes on CI's pgvector image before a push, never after.

**What a posture count covers (corrected 2026-10-25).** Reports above that say "N checks, no
mismatch" (39, 50, 52, 54) counted only two groups of `test_table_posture.py`: each table's
delete/truncate posture for the three API roles (13 tables x 3 = 39) and each `app.*` function's
security-definer flag, owner, `search_path` and EXECUTE grants (15 functions, 54 in all by 0024).
They were not the whole suite, which is 111 checks since 0023 (107 at 0020, 109 at 0021). The other
57 are: every column of `llm_calls` as an API role's INSERT/UPDATE target, 17 columns x 3 roles =
51; the privilege lists of the two minimal roles `app_org_creator` and `app_audit_writer`; the
three checks that every table, `app.*` function and `llm_calls` column has a stated posture; and
that signed-in members can still read their organisation's `llm_calls`. The script that made the
earlier live reads was not kept, so whether those reads also ran any of the 57 is not recorded;
read the earlier figures as the 54 and no more. The first run of all 111 against the live project
was after 0027 (2026-10-25): 111 of 111, with `backend/scripts/live_posture.py` from
2026-10-25 on (read-only, refuses any statement that is not a read). CI runs all 111 on every push,
but against a throwaway database built from the migrations, so it verifies what the migrations
produce, not drift on the project (a dashboard change, a platform change).

**Before pushing a migration that creates or changes an `app.*` function** (the lesson above):
run the migration file against the project inside a transaction that is always rolled back —
since 2026-10-18 with `backend/scripts/live_migration_dry_run.py`, on a fresh backend as the
applying role (`Rules.md` §3.3; the 0022 lesson below) — and inside it read, for every
such function, its owner, `prosecdef`, `proconfig` and
`has_function_privilege(<role>, <function>, 'EXECUTE')` for `anon`, `authenticated` and
`service_role`. They must match what `backend/tests/db/test_table_posture.py` states. Then
confirm nothing persisted (for example, a role the migration creates does not exist after the
rollback). DDL, `CREATE ROLE` included, is transactional, so the dry run leaves no trace. Done for
0012 on 2026-10-16; after the push, the live posture was read again and matched.

0013 (no function, but it changes the grants on the raw log) had the same procedure: a rolled-back
dry run on the live project read every `llm_calls` column's INSERT/UPDATE privilege for the three
API roles (none), the table's SELECT/DELETE/TRUNCATE, both CHECK constraints and the row count
(0); the rollback left the table as it was. Pushed with the CLI 2026-10-17 10:20 UTC; `migration
list` identical; the live posture read after the push matched `test_table_posture.py`.

0014 had the same procedure: the rolled-back dry run showed both columns arriving NOT NULL with
their CHECK constraints, no API role able to write any `llm_calls` column, and 0 rows; the
rollback left the table as it was. Pushed with the CLI 2026-10-17 11:27 UTC; `migration list`
identical; the live posture read after the push matched.

0015–0020 (Phase 3 chunk A; three new `app.*` trigger functions, one new role) had the same
procedure, after CI passed all four jobs on `b694beb` (database suite included). The rolled-back
dry run applied all six files in one transaction and read: the `runs_status_check`; the seven new
`product_claims` CHECKs and four columns; the three triggers; each new function's owner,
`prosecdef`, pinned `search_path` and EXECUTE for the three API roles
(`audit_brand_sector`: security definer, owned by `app_audit_writer`); `app_audit_writer`'s
attributes (no login, no superuser, no BYPASSRLS, no memberships) and privileges (INSERT on
`audit_log`, nothing else); the `audit_writer_insert` and amended `org_creator_insert` policies;
`product_claims` delete/truncate revoked for all three roles; `organisations.is_test` not
updatable by `authenticated`; the nine seeded sectors. All matched `test_table_posture.py`. The
rollback left nothing, the role included. Live rows before the push: 0 runs, 0 claims, 0 sectors,
1 brand. Pushed with the CLI **2026-10-17 14:01 UTC**; `migration list` identical through 0020;
the post-apply read matched the dry run, and a full read of every table's delete/truncate posture
and every `app.*` function (50 of the suite's 107 checks; see "What a posture count covers") against
`test_table_posture.py` found no mismatch.

0021 (rule provenance; two new trigger functions; four synthetic rule rows) had the same
procedure after CI passed on `fb5f2cf`: the rolled-back dry run showed `rules_provenance` arriving
NOT NULL with default `illustrative` on all nine sectors, `source_url` and `created_by` nullable
under the new `sector_constraints_source` CHECK, both trigger functions with pinned `search_path`
as stated, exactly the four synthetic rules (no URL, no author, the fixed source sentence), and no
write grant on `sectors` for `anon` or `authenticated`; the rollback left nothing. Pushed with the
CLI **2026-10-17 16:04 UTC**; `migration list` identical through 0021; the post-apply read matched
and the table and function posture comparison found no mismatch (52 of the suite's 109 checks).

**Lesson (2026-10-18, migration 0022): the dry run passed and the push was refused.** 0022's first
draft set `hnsw.ef_search = 100` on `app.match_memory`. CI passed on `af4dd3a`, the rolled-back dry
run on the live project created the function and called it, and the rollback was clean. The CLI
push then failed at 0022's first statement: `permission denied to set parameter "hnsw.ef_search"
(SQLSTATE 42501)`. Nothing was applied (0023 was never reached; the read-only posture read after
the failure matched the read before). Cause: Postgres lets a non-superuser attach an extension's
setting to a function only once that extension's library is loaded in the backend; before that
the name is an unknown placeholder and the check fails. The session pooler hands a new client a
reused backend: the dry run got one where pgvector was already loaded, the push got a fresh one.
Reproduced on the local container (a non-superuser role, fresh backend: 42501; after loading
pgvector: created). Two changes followed. The setting left the function: it is a search-time
parameter the caller sets per transaction and logs (`Memory.md` §4.3). And the dry run itself was
fixed, not just this migration: `backend/scripts/live_migration_dry_run.py` holds non-fresh
backends open until the pooler gives it a fresh one (started moments ago, pgvector not loaded),
asserts it acts as the URL's own role with no `SET ROLE`, prints every catalog change the files
make inside the rolled-back transaction, and asserts the catalog after the rollback equals the
catalog before (`Rules.md` §3.3). Its first version had the same flaw one level down: its own
catalog snapshot (`pg_get_indexdef` on the HNSW index) loaded pgvector on the apply connection
before the files ran. The snapshot now reads on a separate connection, and freshness is asserted
again immediately before the files. Checked by running the fixed harness on the original 0022
(from `af4dd3a`): refused with the live push's error, where the old harness had passed it. It
supersedes the per-migration scratch scripts used for 0012–0022.

0022 (revised) and 0023 had the same procedure with the fixed harness, after CI passed on
`60bb119`: a fresh backend (five reused ones held first), acting as `postgres` (not a superuser),
still fresh immediately before the files. Inside the rolled-back transaction the only catalog
changes were `app.match_memory` (invoker, `search_path=""` only, EXECUTE for `authenticated` and
`service_role`), `app.memory_items_revocation`, its trigger, and `memory_items_claim_id_org_id_fkey`
re-created without `ON DELETE SET NULL`; the caller's path (`set_config` then the function) worked
as `authenticated` and `service_role` and was refused to `anon`; after the rollback the catalog
equalled the snapshot. Pushed with the CLI **2026-10-18 12:29 UTC**; `migration list` identical
through 0023; the post-apply read matched and the table and function posture comparison found no mismatch (54
of the suite's 111 checks). The two Phase 4 criteria were then checked on the project itself, rolled back
(`Phases.md` Phase 4).

**0024 (decay revocation record).** Dry run 2026-10-20 with `live_migration_dry_run.py` on a fresh
backend as `postgres` (not a superuser): the only catalog changes were
`app.memory_items_revocation`'s body (owner, invoker, `search_path=""` and ACL unchanged) and
`memory_items_check1` replaced by `memory_items_revocation_recorded`. Exercised inside the
rolled-back transaction with E2E Org A: a well-formed decay record accepted with no revoker and
`revoked_at` set by the server; five malformed records refused (no reason, free text, no run,
after ≠ confidence, a decay record with a revoker), a decayed item's confidence change refused, a
signed-in user's decay record refused by the trigger, a person's own revocation still recorded in
their name; the evaluation count excluded E2E Org A (`is_test`). After the rollback the catalog
equalled the snapshot; `memory_items` held 0 rows. Approved by the owner, CI green on `b553c30`,
pushed with the CLI **2026-10-20 10:42 UTC**; `migration list` identical through 0024; the
post-apply read found the new constraint and the function body hash the dry run predicted, and the
table and function posture comparison found no mismatch (54 of the suite's 111 checks).

### Live project — permanent footprint

What the live Supabase project holds that is not evaluation data, and why. Application roles
cannot delete organisations, memberships, runs, LLM calls, memory, campaigns, approvals or audit
rows (0010, 0011), nor claims (0017), so this list only grows; it is kept complete here so that
what is in the database is written down, not reconstructed. **Read on 2026-10-17 at 15:24 UTC**
(read-only), after 0015–0020 and two live E2E runs.

| Rows | What | Why it is there | Excluded from evaluation by |
|---|---|---|---|
| 2 `auth.users` | `e2e-a@` and `e2e-b@brandverse-e2e.example.com`, confirmed | The fixed E2E users (B2): a new user per run would leave undeletable organisations behind | Their organisations' flag (below) |
| 2 `organisations` + 2 `org_members` | `E2E Org A`, `E2E Org B`, each with its user as owner | One org per E2E user; created through `app.create_organisation` by `scripts/ensure_e2e_users.py` | `is_test = true` (0020), set 2026-10-17 by that script for organisations its own users own; every evaluation read filters it (`backend/tests/db/test_test_organisations.py`) |
| 1 `auth.users`, 1 `organisations`, 1 `org_members` | The project owner's account and organisation `Datta` | Phase 1 exit criterion: the owner's own sign-up, through the real form and confirmation email. Kept as the owner's demo and exploration organisation: anything clicked through there is not evaluation data | `is_test = true`, set 2026-10-17 by `scripts/flag_test_organisation.py --owner-email <owner> --name Datta` — found through its owner **and** its name, exactly one match required, so an evaluation organisation the same person owns can never be flagged by it |
| 1 `brands`, 1 `products` | `Capstone Project` (sector not yet chosen) and `SHampoo`, in `Datta` | The Phase 1 sign-up → brand → product evidence, kept in place (`Phases.md` Phase 1) | `Datta`'s `is_test` flag |
| 9 `sectors` | The sector keys (0018), all `rules_provenance = 'illustrative'` (0021) | Registry keys `brands.sector` references | Reference data, not evaluation data; every report naming a sector prints its provenance |
| 4 `sector_constraints` (0021, 2026-10-17 16:04 UTC) | `health-supplements`: prohibited phrase `miracle cure`; prohibited regex `\bguaranteed\s+results?\b`; required disclaimer on all output "Illustrative disclaimer (synthetic rule): consult a qualified professional before use."; required disclaimer triggered by `protein` "Illustrative disclaimer (synthetic rule): protein content varies by serving size." Each: `source_url` null, `source_note` "Synthetic rule, not derived from any regulation. Illustrative only.", `created_by` null | The illustrative rule set that lets the regulated sector run and exercises both rule kinds (§8.1). Retired, never deleted (0002 trigger) — replacing them with a cited set is a deployment step | Reference data, not evaluation data |
| **Run fixture**, 3 rows, created 2026-10-17 15:16 UTC by the first live E2E run after 0017 | In `E2E Org A`: brand `E2E run fixture` (`4bd4db13-97c6-4296-b923-598316c22cdd`, sector `footwear`); product `E2E fixture shoe` (`c1308e7b-0fcd-4d26-9269-e2a6572dbb52`); verified claim "Upper made of recycled mesh" (`deeb1b96-65b0-4b0f-8e30-4a547f813cd5`), evidence `https://example.com/e2e-fixture-spec`, `created_by = 'human'`, `entered_by` = the E2E A user (derived by the 0017 trigger) | From Phase 3 a run needs a product with an active verified claim, so the run-view and tenancy specs need one. Kept and reused (`frontend/e2e/support.ts`, `ensureRunnableProduct`) rather than re-created per run. The claim cannot be deleted (0017). The brand and product have no runs, so the org owner *could* delete them, cascading the claim; the specs never do. The ids are recorded here as a description of what exists, not as a filter | `E2E Org A`'s `is_test` flag |
| 9 `audit_log` rows, then about 3 per full live E2E run | All in `E2E Org A`. First run (15:16–15:17 UTC): `brand.sector_set` × 2 (the two setup specs' brands), `run.blocked` × 1 (`no_verified_claims`), `brand.sector_set` × 3 for fixture brands. Second run (15:22–15:23 UTC): `brand.sector_set` × 2, `run.blocked` × 1 | Every sector change and every gate block is audited (0019, admission); append-only. **Two of the first run's fixture audits belong to duplicate fixture brands** a race in `ensureRunnableProduct` created (it counted brands before the list had loaded); the two empty duplicates were deleted by the backend role at 15:18 UTC, the helper was fixed to wait for each list, and their audit rows remain — the trail of the mistake, not removed | Not read by the evaluation; rows carry `E2E Org A`'s id |

Every live E2E run also creates and then deletes two brands, two products and one claim (deleting
a product with no runs cascades to its claims); none of these remain. Development and smoke LLM
calls never go to the live project; they are logged to the local throwaway database (§4.4,
tally). Live counts at the read above: `brands` 2, `products` 2, `product_claims` 1 (the
fixture's), `audit_log` 9, `runs`, `llm_calls`, `memory_items`, `campaigns`, `approvals` and
`sector_constraints` all 0; after 0021 (16:04 UTC), `sector_constraints` 4. Every organisation on the project is `is_test = true`; none is an
evaluation organisation yet.

A new kind of lasting row added to the live project is added to this table in the same change.

### Database connections from the laptop — session pooler only

The direct host (`db.<ref>.supabase.co`) is IPv6-only on the free tier, and the laptop has no
routable IPv6 (link-local only; checked 2026-10-14), so it cannot reach it. **The laptop worker,
API and every script connect through the Supavisor session pooler** (IPv4, port 5432, user
`postgres.<ref>`) — `DATABASE_URL` holds that string. Not the transaction pooler (port 6543):
LangGraph's checkpointer and psycopg's prepared statements need session semantics.

**Connection limit.** The instance reports `max_connections = 60` (2026-10-14), shared with
Supabase's own services (Auth, Storage, the dashboard, Realtime), and in session mode each client
connection holds one server connection for its whole life. Keep the laptop's total small and
fixed: one pool per process, sized explicitly — about 5 connections across API, ARQ worker and
LangGraph checkpointer together — never a library default, and never one connection per
concurrent agent (agents share the worker's pool). Raise it only with a measured reason.

### Runtime on the laptop — as built (Phase 1 chunk A, 2026-10-16)

From `backend/`, with Docker Desktop running:

```
docker compose up -d redis              # repo root compose.yaml: redis:7.4-alpine, AOF on, bound to 127.0.0.1
uv run python -m app.api                # FastAPI on 127.0.0.1:8000
uv run python -m app.workers            # ARQ worker
uv run python -m scripts.enqueue_ping   # smoke test: a trivial job enqueues and completes
npm run dev -w frontend                 # from the repo root: Next.js on 127.0.0.1:3000
uv run python -m scripts.ensure_e2e_users   # once: the two fixed E2E users + orgs (B2, below)
npm run test:e2e -w frontend            # Playwright against the live project (E2E_AUTH_MODE=supabase)
```

- **End-to-end tests, two environments, same specs (B2, 2026-10-17).** On the laptop they run
  against the live Supabase project as two fixed, confirmed users (`.env.e2e`, git-ignored;
  created once with the secret key by `scripts/ensure_e2e_users.py`, which also flags their
  organisations `is_test`). Permanent footprint: see "Live project — permanent footprint" above —
  the two users and organisations, the run fixture, and a few audit rows per run; each run deletes
  the other brands and products it creates. In CI, which
  holds no live secrets, `scripts/e2e_fake_auth.py` plays Supabase Auth (ES256 tokens, a JWKS,
  sign-in, sign-up pending confirmation) against a database built from the migrations
  (`scripts/e2e_prepare_db.py`); the API verifies those tokens with the production verifier,
  unchanged. The frontend reads only `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL` and
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (`frontend/.env.example`).

- **Frontend (Phase 1 chunk B1, 2026-10-16).** Next.js 15 App Router, run locally against the
  local API (Phase 1 amended: no public URL until Phase 11). The run view (`/runs/{id}`) streams
  with `fetch`, not `EventSource`: it sends `Last-Event-ID` itself when it reconnects, and
  authenticated streaming needs an `Authorization` header, which `EventSource` cannot send. It
  reproduces `EventSource`'s behaviour explicitly — reconnect after the server's `retry:` delay
  (doubling on consecutive failures, capped at 30 s, giving up after 8), resume with
  `Last-Event-ID`, stop quietly on `204`, stop with an error on `404`/`403`. Every event is
  validated against the contract before it reaches the view. The API allows CORS from
  `http://localhost:3000` and `http://127.0.0.1:3000` only (`cors_origins`).
- **Dependencies — how `Rules.md` §1.2 is read (decided 2026-10-16/17).** Packages the documents
  name are the specified stack, not new decisions: fastapi, uvicorn, arq, pydantic-settings, httpx
  and psycopg (`Rules.md` §3.1, this document); Next.js, React, TanStack Query, react-hook-form,
  zod, Tailwind, Vitest and Playwright (`Rules.md` §3.2, §6). **`@supabase/supabase-js` is the
  official client for Supabase Auth, which §2 names, so it falls under the specified stack**
  (confirmed by the project owner 2026-10-17). §1.2's "stop and ask" applies to dependencies the
  documents do not call for; each such approval is recorded in the commit that adds it —
  `cryptography` (ES256 verification), approved 2026-10-16, is the first.
- **Shared contracts.** `backend/scripts/export_contracts.py` writes the run contracts (the 15
  steps and the JSON Schemas of the request, response and events) to
  `frontend/src/contracts/run-contracts.json`. A backend test fails if that file is stale; a
  frontend test fails if the zod schemas disagree with it (fields, required fields, bounds,
  enums). The frontend reads the step list from the file rather than retyping it.

- **Settings** (`app/core/config.py`): pydantic-settings, read once from the environment and the
  repository's `.env`; nothing else reads `os.environ`. `DATABASE_URL` is validated (a
  `postgresql://` URL with a host; the transaction pooler's port 6543 is refused) and never
  echoed in a validation error.
- **Connection budget, enforced at load.** API pool `max 2`, worker pool `max 2`, LangGraph
  checkpointer `1` reserved (Phase 6): 5 in total, and `Settings` refuses any combination over
  `db_connection_budget = 5`. Pools use `min_size = 0` and close connections idle for 120 s, so
  an idle process holds no server connection. Supavisor replaces the client's `application_name`
  with `Supavisor` in `pg_stat_activity`, so per-process attribution is not visible server-side
  (checked 2026-10-16); the budget is enforced by pool sizing, not observed per process.
- **Event loop.** psycopg's async mode refuses Windows' default `ProactorEventLoop`, so the API
  (uvicorn `loop="asyncio:SelectorEventLoop"`) and the worker (`asyncio.run(..., loop_factory=
  SelectorEventLoop)`) both run on a selector loop.
- **`127.0.0.1`, not `localhost`.** On this laptop `localhost` tries `::1` first and takes about
  2 s to fall back (measured 2026-10-16: 2.08 s against 0.02 s), longer than ARQ's 1 s connect
  timeout. Redis defaults to `redis://127.0.0.1:6379/0`.
- **Jobs are JSON, not pickle.** ARQ's serializer is replaced; every payload is a Pydantic model
  dumped to JSON and validated again by the job.
- **Run progress** is a Redis Stream per run (`run:{id}:events`, TTL 7 days, MAXLEN ~1000). The
  API writes `run.queued` before enqueueing; the worker writes the rest. The SSE endpoint replays
  and then follows the stream: SSE `event` is the event type (`run.queued`, `run.started`,
  `step`, `run.completed`, `run.failed`), `id` is the stream entry ID (`Last-Event-ID` resumes
  after it; reconnecting after the final event gets `204`, which stops `EventSource`), `data` is
  the event as JSON. Step events carry `step` (1–15), `name` and `status`
  (`started`/`completed`/`skipped`/`failed`). Step names come from `app/core/pipeline.py`, which a
  test keeps in line with §5.1; step 6 is `research` (`focused_research`/`deep_research`, skipped
  at D0) and step 7 is `run_agents` (`select_agents` then the fan-out).
- **Logs** are JSON lines on stdout, `run_id` bound per run through a context variable,
  ASCII-escaped (a redirected stdout on Windows is cp1252 and ARQ logs `→`). ARQ's own lines
  carry `run_id` too: the worker's `on_job_start` hook binds it from the job ID
  (`fake-run:{run_id}`) inside the job's task, before ARQ logs the job's start. Lines ARQ logs
  before that hook (job expired, undecodable, max retries exceeded) have no `run_id`.

**Authentication (Phase 1 B2, 2026-10-16/17).** The unauthenticated stream guard of chunk A
(fake runs only, `403` otherwise) was removed in the same change that added authentication and
the organisation check, together with its two tests, as this section required.

- **Every endpoint except `/health` needs a signed-in caller** (`401` with
  `WWW-Authenticate: Bearer` otherwise). The token is a Supabase access token, verified **locally**:
  ES256 only (the algorithm is pinned before any key lookup, so `none` and HS256 are refused),
  against the project's public key set (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), with
  `exp`, `iat`, `sub`, `aud = authenticated`, `iss = {SUPABASE_URL}/auth/v1` required and
  `role = authenticated`. The API never calls Supabase Auth per request.
- **Key rotation.** The key set is cached (re-read after `jwks_ttl_s`, 1 hour). A token whose
  `kid` the cache does not know triggers **one** refetch before it is rejected, so a rotation
  does not break every request until a restart. That refetch is rate-limited
  (`jwks_min_refetch_interval_s`, 30 s): a flood of tokens with invented `kid`s costs Supabase at
  most one request per interval. A known `kid` with a bad signature is rejected with no refetch.
  If Supabase is unreachable, the cached keys keep serving.
- **Known limitation — the revocation window is 3,600 seconds.** Local verification cannot see a
  revoked session: after a user signs out (or is removed), their access token stays valid until
  it expires. The project's access-token lifetime, measured 2026-10-17 from real tokens issued to
  the E2E users (`exp − iat` = `expires_in` = 3600 s), is that window. Asking Supabase per request
  would close it at the cost of a round trip per call; the API does not, by decision
  (2026-10-16). An open SSE stream is authorised when it opens and runs to the run's end.
- **The organisation check.** Tenant reads and writes run **as the caller**: in one transaction,
  `role` is set to `authenticated` and `request.jwt.claims` to the verified claims, exactly as
  PostgREST does, so row-level security decides; every query also names its `org_id`
  (`Rules.md` P3). A run's organisation is recorded in its first event (`run.queued.org_id`,
  written by the API; fake runs never touch the `runs` table, whose rows can never be deleted).
  Streaming a run needs membership of that organisation, resolved by migration 0003's
  `app.member_org_ids()` under the caller's identity; starting one needs owner or editor
  (`app.has_role`), as `runs_insert` requires. **A non-member gets the same `404` as for a run
  that does not exist**, before any resume logic, so a run's existence is not disclosed across
  organisations. Tests: `backend/tests/db/test_api.py` (merge-blocking with the database suite).
- **Creating an organisation** goes through `app.create_organisation(name)` (migration 0012): the
  caller becomes its owner; it takes the name only. **Accepted limitation (2026-10-16): nothing
  limits how many organisations one user creates** — a thesis project, not a public service.
- **Secrets.** The browser holds only the publishable key and the user's own access token. The
  secret key is read only by local scripts (`backend/scripts/ensure_e2e_users.py`); a frontend
  test fails if it, or any `sb_secret_` value, appears in the built client bundle (`Rules.md` P2).

### Storage per `memory_items` row

**Measured 2026-10-14** by `backend/scripts/measure_memory_storage.py` in CI's database job: GitHub
Actions run 38578508502 at commit `c00371d`, job 115901867221. Conditions: PostgreSQL 17.11 with
pgvector 0.8.7 (`pgvector/pgvector:pg17`, pinned by digest), the real migrations, 10,000 rows,
400-character content, random 768-dimension embeddings (3,076 bytes each; incompressible, like real
ones), after `vacuum analyze`. Runner: the job was labelled `ubuntu-latest`, which at that date
resolved to ubuntu-24.04 per GitHub's runner-images notice; the exact image version is in the job
log, which needs admin access, and was not read. CI has since been pinned to `ubuntu-24.04`.

| Component | Bytes per row |
|---|---|
| Heap (400-character content) | 683 |
| TOAST (the embedding, stored out of line) | 4,147 |
| HNSW index (`m = 16, ef_construction = 64`) | 4,097 |
| GIN full-text index | 510 |
| B-tree `(org_id, brand_id, kind) where not revoked` | 9 |
| Primary key | 43 |
| **Total (`pg_total_relation_size`)** | **9,495** |

The earlier estimate (HNSW about 4,100 B/row, calculated from pgvector's on-disk layout; total about
9,500) is superseded; the measurement agrees with it. At 9,495 B/row, 500 MB holds roughly
52,000–55,000 rows (depending on whether Supabase's MB is 10⁶ or 2²⁰ bytes). The evaluation's worst
case (140 approved runs × 100 items written + 620 seed and relevance-set items, about 14,600 rows)
is about 139 MB. The larger unknowns are LangGraph checkpoints (Phase 6) and any persisted
research findings (Phase 5); both are measured before the evaluation.

**Environment variables**

```
SUPABASE_URL=                 SUPABASE_PUBLISHABLE_KEY=
SUPABASE_SECRET_KEY=          DATABASE_URL=
GOOGLE_API_KEY=               LITELLM_MASTER_KEY=
REDIS_URL=                    SEARXNG_URL=
LANGGRAPH_STRICT_MSGPACK=true
MAX_COST_PER_RUN_USD=2.50
MAX_LLM_CALLS_PER_RUN=20
# EMBEDDING_MODEL intentionally omitted — model IDs live only in config/models.yaml (Rules.md P5)
```

---

## 12. Observability

Every run emits a structured trace: step name, duration, model, tokens, cost, memory hits, decisions taken and why. Logs are JSON, correlated by `run_id`.

The admin dashboard surfaces: cost per run over time, D0/D1/D2 distribution, memory hit rate, agent failure rate, and cap breaches. **The D0/D1/D2 distribution shifting toward D0 as memory accumulates is the visual proof of the thesis.** Put that chart in the viva deck.
