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

**Source scoring** combines domain authority, recency, specificity to the query, and corroboration count. Findings below threshold are discarded, not passed to agents — unscored sources are how hallucinated authority enters a system.

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
| **Risk** | Restricted claims, regulated-sector language, superlatives without proof, 8 predatory marketing anti-patterns. **Registry:** full copy scanned for every active `prohibited_phrase`; every applicable `required_disclaimer` must be present. | `pass` / `block` |

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

The anti-pattern blacklist (from AMAD-MS gap G6): false scarcity, fabricated social proof, undisclosed paid endorsement, vulnerability targeting, dark-pattern consent, unsubstantiated health claims, price anchoring on fictional prices, confirmshaming.

---

## 9. Data model

Full DDL. **`docs/erd.md` is canonical** — it records why each column and constraint exists
(issues E1–E13) and the role matrix. This section must match it; change both in the same commit.
The SQL below is `supabase/migrations/` concatenated in order; a test
(`backend/tests/test_schema_doc_sync.py`) fails if the two ever differ.

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
> State on the project after migration 0011 (checked 2026-10-14, 39 of 39 posture checks
> matching `backend/tests/db/test_table_posture.py`):
>
> | Table | Claim | Holds against the application roles | Holds against `postgres` |
> |---|---|---|---|
> | `audit_log` | append-only | Yes (grants revoked; trigger also blocks `truncate`) | No |
> | `approvals` | append-only | Yes: direct `update`/`delete`/`truncate` revoked, and no application role can delete the `runs` or `organisations` rows that would cascade into it (0010) | No |
> | `memory_items` | never hard-deleted (`Memory.md` §6.4) | Yes: no direct delete or truncate (0010), and a brand that has memory items cannot be deleted (0011) | No |
> | `org_members` | roles revoked, never deleted | Yes (0010) | No |
> | `sector_constraints` | never edited in place or deleted | Yes: `update`/`delete` blocked by trigger, `delete`/`truncate` revoked (0011) | No |
> | `llm_calls`, `runs` | raw logs, retained (`Rules.md` A1) | Yes (0010, 0011) | No |
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
| Monthly spend | `organisations.monthly_budget_cap_usd` | Pre-flight, before every LLM call: the estimated cost must fit within **both** the run's remaining budget and the organisation's remaining month-to-date budget (`sum(llm_calls.cost_usd)` for the org in the current UTC calendar month). A breach yields `budget_exhausted`, as for the run cap. |
| Run starts | `organisations.rate_limit_runs_per_hour` | At `POST /api/v1/runs`, Redis counter per org; over the limit → `429`. |
| API requests | `organisations.rate_limit_requests_per_minute` | At the API gateway, Redis counter per org; over the limit → `429`. |

The column defaults (50.00 USD, 10 runs/hour, 60 requests/minute) are **placeholders, not derived
values**; set them per organisation and report any values used in evaluation.

```sql
-- ═══ supabase/migrations/0001_extensions.sql ═══
-- 0001: extensions and the app helper schema.
-- Down (destructive; only on a database with no BrandverseAI data):
--   drop schema app cascade; drop extension pg_trgm; drop extension vector;
create extension if not exists vector;
create extension if not exists pg_trgm;
create schema if not exists app;
-- RLS policies call app.member_org_ids() and app.has_role() as the caller's role, so the API
-- roles need USAGE.
grant usage on schema app to anon, authenticated, service_role;

-- ═══ supabase/migrations/0002_sectors_and_registry.sql ═══
-- 0002: sectors and the sector constraint registry (Architecture.md §8.1). Platform reference
-- data, not tenant data.
-- Down: drop table sector_constraints; drop table sectors;
--   drop function app.sector_constraints_versioned();
create table sectors (
  id text primary key,                              -- e.g. 'health-supplements'
  label text not null,
  regulated boolean not null default false,         -- Rules.md T4
  created_at timestamptz not null default now()
);

-- Human-authored only; versioned, never edited in place.
create table sector_constraints (
  id uuid primary key default gen_random_uuid(),
  sector_id text not null references sectors(id),
  jurisdiction text not null default 'IN',
  kind text not null check (kind in ('required_disclaimer','prohibited_phrase')),
  match_type text not null check (match_type in ('phrase','regex')),
  pattern text,               -- prohibited_phrase: wording that must not appear
                              -- required_disclaimer: trigger; null = required on all output
  disclaimer_text text,       -- required_disclaimer only: exact text that must appear
  source_url text not null,   -- the regulation or guidance this rule comes from
  source_note text,
  effective_from date not null,
  retired_at date,
  created_by uuid not null references auth.users(id),   -- a human; agent ids are not uuids
  created_at timestamptz not null default now(),
  check ((kind = 'prohibited_phrase'   and pattern is not null and disclaimer_text is null)
      or (kind = 'required_disclaimer' and disclaimer_text is not null)),
  check (retired_at is null or retired_at >= effective_from)
);

create function app.sector_constraints_versioned() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'sector_constraints rows are retired, never deleted';
  end if;
  if (to_jsonb(new) - 'retired_at') is distinct from (to_jsonb(old) - 'retired_at') then
    raise exception 'sector_constraints rows are versioned: only retired_at may change';
  end if;
  return new;
end $$;
create trigger sector_constraints_versioned before update or delete on sector_constraints
  for each row execute function app.sector_constraints_versioned();

revoke insert, update, delete, truncate on sectors, sector_constraints
  from public, anon, authenticated;                                       -- admin path only

alter table sectors            enable row level security;
alter table sector_constraints enable row level security;
create policy sectors_read on sectors for select to authenticated using (true);
create policy sector_constraints_read on sector_constraints for select to authenticated using (true);

-- ═══ supabase/migrations/0003_tenancy_and_rbac.sql ═══
-- 0003: organisations, membership roles (RBAC) and the role check used by every tenant policy.
-- Down: drop function app.has_role(uuid, text[]); drop function app.member_org_ids();
--   drop table org_members; drop table organisations;
create table organisations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  monthly_budget_cap_usd numeric(10,2) not null default 50.00
    check (monthly_budget_cap_usd > 0),
  rate_limit_runs_per_hour int not null default 10 check (rate_limit_runs_per_hour > 0),
  rate_limit_requests_per_minute int not null default 60
    check (rate_limit_requests_per_minute > 0),
  created_at timestamptz default now()
);

-- One row per (org, user, role); a user may hold several roles. Revoked, never deleted.
create table org_members (
  org_id uuid not null references organisations(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  role text not null check (role in ('owner','editor','approver','viewer')),
  granted_by uuid references auth.users(id),
  granted_at timestamptz not null default now(),
  revoked_by uuid references auth.users(id),
  revoked_at timestamptz,
  primary key (org_id, user_id, role),
  check ((revoked_at is null) = (revoked_by is null))
);

-- Membership is resolved from org_members keyed on auth.uid(); there is no org claim in the JWT,
-- so a user may belong to several organisations (agency persona, PRD.md §3) and no custom access
-- token hook is needed. Both functions are stable security definer, so they read org_members past
-- its own RLS. auth.uid() is wrapped as (select auth.uid()) so it is evaluated once, not per row.

-- Organisations in which the caller holds any unrevoked role. Policies call it as
-- `org_id in (select app.member_org_ids())`, an uncorrelated subquery evaluated once per
-- statement, not once per row.
create function app.member_org_ids() returns setof uuid
language sql stable security definer set search_path = '' as $$
  select distinct m.org_id from public.org_members m
  where m.user_id = (select auth.uid()) and m.revoked_at is null;
$$;

-- The caller holds one of the roles in the row's own org. Used where a policy needs a specific
-- role; reads use app.member_org_ids(). No parent-table walking.
create function app.has_role(p_org uuid, p_roles text[]) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.org_members m
                 where m.org_id = p_org and m.user_id = (select auth.uid())
                   and m.role = any (p_roles) and m.revoked_at is null);
$$;

alter table organisations enable row level security;
alter table org_members   enable row level security;

create policy org_read on organisations for select to authenticated
  using (id in (select app.member_org_ids()));
create policy org_owner_update on organisations for update to authenticated
  using (app.has_role(id, array['owner'])) with check (app.has_role(id, array['owner']));

create policy members_read on org_members for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy members_owner_insert on org_members for insert to authenticated
  with check (app.has_role(org_id, array['owner']));
create policy members_owner_update on org_members for update to authenticated
  using (app.has_role(org_id, array['owner'])) with check (app.has_role(org_id, array['owner']));

-- ═══ supabase/migrations/0004_product_truth.sql ═══
-- 0004: brands, products and the product truth store.
-- Down: drop table product_claims; drop table products; drop table brands;
create table brands (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  name text not null,
  voice_guidelines text,
  voice_embedding vector(768),
  voice_embedding_model text,                                     -- E3
  voice_embedding_version text,                                   -- E3
  sector text references sectors(id),                             -- registry key; no free text
  created_at timestamptz default now(),
  unique (id, org_id),
  check (voice_embedding is null
         or (voice_embedding_model is not null and voice_embedding_version is not null))
);

create table products (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  brand_id uuid not null,
  name text not null,
  category text,
  attributes jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  unique (id, org_id),
  foreign key (brand_id, org_id) references brands (id, org_id) on delete cascade
);

-- The product truth store: the spine of anti-hallucination
create table product_claims (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  product_id uuid not null,
  claim text not null,
  claim_type text not null check (claim_type in ('verified','deduced','restricted')),
  evidence_url text,
  evidence_note text,
  confidence numeric(3,2) check (confidence between 0 and 1),
  created_by text not null,                 -- 'human' | agent id
  created_at timestamptz default now(),
  unique (id, org_id),                                                    -- target for E5
  foreign key (product_id, org_id) references products (id, org_id) on delete cascade
);

alter table brands         enable row level security;
alter table products       enable row level security;
alter table product_claims enable row level security;

-- read any role; write owner/editor; delete owner
create policy brands_read   on brands for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy brands_insert on brands for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy brands_update on brands for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy brands_delete on brands for delete to authenticated
  using (app.has_role(org_id, array['owner']));

create policy products_read   on products for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy products_insert on products for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy products_update on products for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy products_delete on products for delete to authenticated
  using (app.has_role(org_id, array['owner']));

create policy claims_read   on product_claims for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy claims_insert on product_claims for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy claims_update on product_claims for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy claims_delete on product_claims for delete to authenticated
  using (app.has_role(org_id, array['owner']));

-- ═══ supabase/migrations/0005_runs_and_llm_calls.sql ═══
-- 0005: runs and the per-call LLM log the evaluation harness and org budget read.
-- Down: drop table llm_calls; drop table runs;
create table runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  product_id uuid,
  arm text not null default 'proposed'      -- 'baseline_a' | 'baseline_b' | 'proposed'
    check (arm in ('baseline_a','baseline_b','proposed')),
  status text not null default 'queued',
  request jsonb not null,
  depth text,
  research_score numeric(4,3),
  selected_agents text[],
  memory_hit_count int default 0,
  search_count int default 0,
  llm_call_count int default 0,
  total_cost_usd numeric(10,4) default 0,
  latency_ms int,
  budget_exhausted boolean default false,
  created_at timestamptz default now(),
  completed_at timestamptz,
  unique (id, org_id),
  foreign key (product_id, org_id) references products (id, org_id)     -- E7
);

-- Every call. The evaluation harness reads this table; org month-to-date spend sums it.
create table llm_calls (
  id bigserial primary key,
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  run_id uuid not null,
  agent_id text,
  model text not null,
  tier text not null,
  route_reason text not null,
  input_tokens int, output_tokens int,
  cost_usd numeric(10,6),
  latency_ms int,
  created_at timestamptz default now(),
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade
);
create index on llm_calls (org_id, created_at);

alter table runs      enable row level security;
alter table llm_calls enable row level security;

-- runs: read any role; start a run owner/editor. Status updates come from the backend.
create policy runs_read   on runs for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy runs_insert on runs for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));

create policy llm_calls_read on llm_calls for select to authenticated
  using (org_id in (select app.member_org_ids()));

-- ═══ supabase/migrations/0006_memory_items.sql ═══
-- 0006: marketing memory (Memory.md).
-- Indexes are created without CONCURRENTLY: the table is new and empty here, and CONCURRENTLY
-- cannot run inside a migration transaction. Later indexes on this table must use it (Rules.md §3.3).
-- Down: drop table memory_items;
create table memory_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  brand_id uuid,
  product_id uuid,
  claim_id uuid,                                                          -- E5
  kind text not null,                       -- persona | competitor | insight | claim | creative | sector
  content text not null,
  embedding vector(768),
  embedding_model text,                                                   -- E2
  embedding_version text,                                                 -- E2
  sector text references sectors(id),
  source_url text,
  provenance text not null,                 -- run id, agent, or 'human'
  confidence numeric(3,2),
  freshness_days int,                       -- after which it decays
  approved boolean default false,
  revoked boolean default false,
  revoked_by uuid,                                                        -- E4
  revoked_at timestamptz,                                                 -- E4
  revoked_reason text,                                                    -- E4
  is_seed boolean not null default false,                                 -- E6
  created_at timestamptz default now(),
  last_used_at timestamptz,
  foreign key (brand_id, org_id)   references brands (id, org_id) on delete cascade,               -- E7
  foreign key (product_id, org_id) references products (id, org_id)
    on delete set null (product_id),                                                               -- E7
  foreign key (claim_id, org_id)   references product_claims (id, org_id)
    on delete set null (claim_id),                                                                 -- E5
  check (embedding is null
         or (embedding_model is not null and embedding_version is not null)),
  check (not revoked or (revoked_by is not null and revoked_at is not null))
);

-- As specified in Memory.md §4.3. m and ef_construction equal pgvector's defaults; stated so the
-- migration and the spec cannot drift. The btree serves match_memory's scope (org_id, brand_id,
-- not revoked), which every retrieval applies first (Rules.md P3).
create index on memory_items using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
create index on memory_items using gin (to_tsvector('english', content));
create index on memory_items (org_id, brand_id, kind) where not revoked;

revoke update on memory_items from authenticated;                         -- revocation columns only
grant update (revoked, revoked_by, revoked_at, revoked_reason) on memory_items to authenticated;

alter table memory_items enable row level security;

-- read any role; owner/editor may revoke (column grant above limits what they can set), and only
-- in their own name. (select auth.uid()) is evaluated once per statement, not once per row.
create policy memory_read   on memory_items for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy memory_revoke on memory_items for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']) and revoked_by = (select auth.uid()));

-- ═══ supabase/migrations/0007_campaigns.sql ═══
-- 0007: campaigns and the approval constraint (Rules.md T6).
-- Down: drop table campaigns; drop function app.campaigns_active_approver();
create table campaigns (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  run_id uuid not null,
  strategy jsonb, creative jsonb, validation jsonb,
  status text default 'draft'
    check (status in ('draft','awaiting_approval','approved','rejected')),
  approved_by uuid,
  approver_role text generated always as ('approver') stored,
  approved_at timestamptz,
  quality_scores jsonb,
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade,
  -- approved_by must be a member of THIS org holding the approver role (Rules.md T6)
  foreign key (org_id, approved_by, approver_role)
    references org_members (org_id, user_id, role),
  check (status <> 'approved' or (approved_by is not null and approved_at is not null))
);

-- The role FK cannot see revoked_at; a revoked approver is rejected here.
create function app.campaigns_active_approver() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.approved_by is not null
     and (tg_op = 'INSERT'
          or new.approved_by is distinct from old.approved_by
          or new.status is distinct from old.status)
     and not exists (select 1 from public.org_members m
                     where m.org_id = new.org_id and m.user_id = new.approved_by
                       and m.role = 'approver' and m.revoked_at is null) then
    raise exception 'approved_by must hold an active approver role in this organisation';
  end if;
  return new;
end $$;
create trigger campaigns_active_approver before insert or update on campaigns
  for each row execute function app.campaigns_active_approver();

alter table campaigns enable row level security;

-- read any role. Approval is written by the backend; the FK + trigger enforce that
-- approved_by is an active approver of this org regardless of who writes it.
create policy campaigns_read on campaigns for select to authenticated
  using (org_id in (select app.member_org_ids()));

-- ═══ supabase/migrations/0008_audit_log.sql ═══
-- 0008: append-only audit log.
-- Not reversible by design: dropping the audit trail destroys evidence. A rollback leaves it.
create table audit_log (
  id bigint generated always as identity primary key,
  occurred_at timestamptz not null default now(),
  org_id uuid,              -- null for platform actions; no FK: the log outlives the org
  actor_type text not null check (actor_type in ('user','service','agent')),
  actor_id text not null,   -- auth user id, service name, or agent id
  action text not null,
  entity_type text not null,
  entity_id text not null,
  details jsonb not null default '{}'::jsonb
);

create function app.audit_log_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'audit_log is append-only: % is not permitted', tg_op;
end $$;
create trigger audit_log_no_update_delete before update or delete on audit_log
  for each row execute function app.audit_log_append_only();
create trigger audit_log_no_truncate before truncate on audit_log
  for each statement execute function app.audit_log_append_only();

revoke update, delete, truncate on audit_log from public, anon, authenticated, service_role;
revoke insert on audit_log from public, anon, authenticated;              -- backend writes only

alter table audit_log enable row level security;
create policy audit_owner_read on audit_log for select to authenticated
  using (org_id is not null and app.has_role(org_id, array['owner']));

-- ═══ supabase/migrations/0009_approvals.sql ═══
-- 0009: approval decisions (docs/approval-rubric.md §5). One row per decision, including revise,
-- reject and undecided, which campaigns.approved_by cannot record. A revised campaign that comes
-- back gets a further row. Kept apart from campaigns.quality_scores, which holds the two blind
-- raters' scores (rubric §6), so an approver's decision and a rater's score are never mixed.
-- Calibration decisions (rubric §6) are stored here too, flagged is_calibration, and excluded from
-- evaluation by the harness (app/evaluation/approvals.py) — the same pattern as seed memory
-- (Memory.md §7.1, memory_items.is_seed). The flag, not the practice org's id, is the filter:
-- org ids are environment-specific and change on a reseed.
-- Down: drop table approvals; drop function app.approvals_active_approver();
--   drop function app.approvals_practice_is_calibration();
--   alter table campaigns drop constraint campaigns_id_org_id_key;
--   alter table organisations drop column is_practice;
--   grant update on organisations to authenticated;
alter table campaigns add constraint campaigns_id_org_id_key unique (id, org_id);  -- FK target

-- The calibration practice organisation (rubric §6), marked by a flag rather than identified by
-- id, so the rule below survives a reseed. Set by the backend only: owners keep update on the
-- other organisation columns but not on this one.
alter table organisations add column is_practice boolean not null default false;
revoke update on organisations from authenticated;
grant update (name, monthly_budget_cap_usd, rate_limit_runs_per_hour,
              rate_limit_requests_per_minute) on organisations to authenticated;

create table approvals (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  org_id uuid not null references organisations(id) on delete cascade,
  approver_id uuid not null,
  approver_role text generated always as ('approver') stored,
  outcome text not null check (outcome in ('approve','revise','reject','undecided')),
  failed_gate text,                         -- rubric gate id, e.g. 'T1.3'; null on approve
  reason text,                              -- required on anything but approve (rubric §5)
  seconds_spent int not null check (seconds_spent >= 0),
  arm_inferred boolean not null default false,
  arm_inference_note text,
  decided_at timestamptz not null default now(),
  is_calibration boolean not null default false,          -- rubric §6 practice row
  foreign key (campaign_id, org_id) references campaigns (id, org_id) on delete cascade,
  -- approver_id must be a member of THIS org holding the approver role (Rules.md T6)
  foreign key (org_id, approver_id, approver_role)
    references org_members (org_id, user_id, role),
  check (outcome = 'approve' or nullif(btrim(reason), '') is not null),
  check (outcome <> 'approve' or failed_gate is null),
  check (not arm_inferred or nullif(btrim(arm_inference_note), '') is not null)
);
create index on approvals (campaign_id, decided_at);

-- The role FK cannot see revoked_at; a revoked approver is rejected here.
create function app.approvals_active_approver() returns trigger
language plpgsql set search_path = '' as $$
begin
  if not exists (select 1 from public.org_members m
                 where m.org_id = new.org_id and m.user_id = new.approver_id
                   and m.role = 'approver' and m.revoked_at is null) then
    raise exception 'approver_id must hold an active approver role in this organisation';
  end if;
  return new;
end $$;
create trigger approvals_active_approver before insert on approvals
  for each row execute function app.approvals_active_approver();

-- Every decision in a practice organisation is a calibration decision, so an unflagged practice
-- row cannot exist to leak into evaluation. The harness also excludes practice orgs.
create function app.approvals_practice_is_calibration() returns trigger
language plpgsql set search_path = '' as $$
begin
  if not new.is_calibration
     and exists (select 1 from public.organisations o
                 where o.id = new.org_id and o.is_practice) then
    raise exception 'approvals in a practice organisation must have is_calibration = true';
  end if;
  return new;
end $$;
create trigger approvals_practice_is_calibration before insert or update on approvals
  for each row execute function app.approvals_practice_is_calibration();

-- A decision is evidence (rubric §7: amendments are recorded, never silent). Rows are appended,
-- never edited: a re-score is a new row.
revoke update, delete, truncate on approvals from public, anon, authenticated, service_role;

alter table approvals enable row level security;

-- read any role. Decisions are written by the backend; the FK + trigger enforce that
-- approver_id is an active approver of this org regardless of who writes it.
create policy approvals_read on approvals for select to authenticated
  using (org_id in (select app.member_org_ids()));

-- ═══ supabase/migrations/0010_revoke_app_role_deletes.sql ═══
-- 0010: correctness fix (Architecture.md §9, stated limitation; Memory.md §6.4).
-- The application roles could delete rows the design says are never deleted: service_role could
-- hard-delete memory_items, and deleting a run or an organisation cascaded into campaigns and
-- approvals, defeating approvals' append-only property. Deleting from these tables is now a
-- deliberate postgres operation (Architecture.md §9, "Deleting an organisation").
-- TRUNCATE is revoked with DELETE: it removes rows without any row-level check.
-- Down: grant delete, truncate on memory_items, runs, campaigns, organisations, org_members
--   to anon, authenticated, service_role;
revoke delete, truncate on memory_items, runs, campaigns, organisations, org_members
  from public, anon, authenticated, service_role;

-- ═══ supabase/migrations/0011_close_remaining_delete_paths.sql ═══
-- 0011: correctness fix, completing 0010 (Architecture.md §9, stated limitation).
-- 1. llm_calls are the evaluation's raw logs (Rules.md A1); sector_constraints rows are retired,
--    never deleted, and their trigger fires on DELETE only, not TRUNCATE; sectors are the keys the
--    registry and brands depend on. No application role deletes or truncates any of them.
-- 2. TRUNCATE ignores row-level security; Supabase's default privileges grant it to every API
--    role on every new table. Revoked on the remaining tables, so no application role holds it
--    anywhere (backend/tests/db/test_table_posture.py states the posture of every table).
-- 3. memory_items.brand_id becomes ON DELETE NO ACTION: deleting a brand no longer hard-deletes
--    its memory (Memory.md §6.4). A brand with memory items cannot be deleted — the same rule as a
--    product with runs. Deleting the whole organisation (a postgres procedure) still works: the
--    cascade removes its memory items before the check runs at the end of the statement.
-- Down: grant delete, truncate on llm_calls, sector_constraints, sectors to anon, authenticated,
--   service_role; grant truncate on brands, products, product_claims to anon, authenticated,
--   service_role; re-create memory_items_brand_id_org_id_fkey with ON DELETE CASCADE.
revoke delete, truncate on llm_calls, sector_constraints, sectors
  from public, anon, authenticated, service_role;
revoke truncate on brands, products, product_claims
  from public, anon, authenticated, service_role;

alter table memory_items
  drop constraint memory_items_brand_id_org_id_fkey,
  add constraint memory_items_brand_id_org_id_fkey
    foreign key (brand_id, org_id) references brands (id, org_id);

-- ═══ supabase/migrations/0012_create_organisation.sql ═══
-- 0012: a signed-in user creates an organisation and becomes its owner.
--
-- Before this, a new user had no way to their first organisation: `organisations` has no insert
-- policy, and adding a member needs an existing owner. app.create_organisation is that way.
--
-- 1. NAME ONLY. The owner is the caller, derived inside from the request's JWT claims. A user-id
--    parameter would let any signed-in caller mint an owner membership for someone else.
-- 2. search_path pinned, as on every app.* function.
-- 3. Security definer with the minimum rights needed. The function is owned by app_org_creator,
--    a NOLOGIN role holding INSERT on organisations and org_members and nothing else: no SELECT,
--    UPDATE, DELETE or TRUNCATE anywhere, no BYPASSRLS. Row-level security still binds it; its
--    two insert policies admit only the rows this function writes. No API role is a member of it.
--
-- Why the caller is not read with auth.uid(): Supabase's postgres role cannot grant USAGE on
-- schema auth (checked 2026-10-16: no grant option), so app_org_creator cannot call it. The
-- function reads the same claims with the same expression instead, and this migration refuses to
-- apply on a platform whose auth.uid() differs from that expression; a database test asserts the
-- two agree for every claim shape (backend/tests/db/test_create_organisation.py).
--
-- Accepted limitation (2026-10-16): nothing limits how many organisations one user creates.
-- Down: drop function app.create_organisation(text);
--   drop policy org_creator_insert on organisations; drop policy members_creator_insert on org_members;
--   revoke all on organisations, org_members from app_org_creator;
--   revoke usage on schema public, app from app_org_creator; drop role app_org_creator;

-- The expression below must be auth.uid()'s, exactly (whitespace aside).
do $$
begin
  if pg_catalog.regexp_replace(
       (select p.prosrc from pg_catalog.pg_proc p where p.oid = 'auth.uid()'::regprocedure),
       '\s+', '', 'g')
     <> 'selectcoalesce(nullif(current_setting(''request.jwt.claim.sub'',true),''''),(nullif(current_setting(''request.jwt.claims'',true),'''')::jsonb->>''sub''))::uuid'
  then
    raise exception '0012: auth.uid() no longer matches the claim expression app.create_organisation mirrors; review before applying';
  end if;
end $$;

-- Roles are cluster-wide; create once (a re-created test database meets an existing role).
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'app_org_creator') then
    create role app_org_creator nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;

grant usage on schema public, app to app_org_creator;
grant insert on organisations, org_members to app_org_creator;

create policy org_creator_insert on organisations for insert to app_org_creator
  with check (not is_practice);
create policy members_creator_insert on org_members for insert to app_org_creator
  with check (role = 'owner' and granted_by = user_id and revoked_at is null);

create function app.create_organisation(p_name text) returns uuid
language plpgsql volatile security definer set search_path = '' as $$
declare
  -- auth.uid(), expression for expression (see the header and the check above).
  v_owner uuid := coalesce(
    nullif(pg_catalog.current_setting('request.jwt.claim.sub', true), ''),
    (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid;
  v_name text := pg_catalog.btrim(p_name);
  v_org uuid := pg_catalog.gen_random_uuid();
begin
  if v_owner is null then
    raise exception 'create_organisation: not signed in' using errcode = '42501';
  end if;
  if v_name is null or pg_catalog.char_length(v_name) not between 1 and 120 then
    raise exception 'create_organisation: name must be 1 to 120 characters' using errcode = '22023';
  end if;
  insert into public.organisations (id, name) values (v_org, v_name);
  insert into public.org_members (org_id, user_id, role, granted_by)
    values (v_org, v_owner, 'owner', v_owner);
  return v_org;
end;
$$;

-- Callable by signed-in users only (functions are executable by PUBLIC by default, and Supabase
-- grants the API roles more). Settled BEFORE the owner changes: afterwards the migration role is
-- no longer the owner, and its REVOKE would silently match nothing (found in a rolled-back dry
-- run on the project, 2026-10-16; a superuser, as in CI, does not show it).
revoke all on function app.create_organisation(text) from public, anon, service_role;
grant execute on function app.create_organisation(text) to authenticated;

-- Hand the function to its minimal owner. Changing owner needs SET ROLE on the new owner and
-- CREATE on the schema; both are granted for the change only. (On PostgreSQL 16+ the role's
-- creator keeps the automatic ADMIN membership; postgres already holds every right the role has.)
grant app_org_creator to current_user;
grant create on schema app to app_org_creator;
alter function app.create_organisation(text) owner to app_org_creator;
revoke create on schema app from app_org_creator;
revoke app_org_creator from current_user;
```

---

## 10. API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs` | Start a campaign run → `202` + `run_id` |
| `GET` | `/api/v1/runs/{id}` | Status, current step, budget consumed |
| `GET` | `/api/v1/runs/{id}/stream` | SSE step-by-step progress for the live pipeline UI. Signed-in members of the run's organisation only (`404` otherwise; §11, Authentication) |
| `POST` | `/api/v1/runs/fake` | Phase 1: start a fake 15-step run (no LLM, no pipeline) → `202` + `run_id`; never mounted in production |
| `GET` | `/health`, `/health/ready` | Liveness (no dependencies); readiness (Redis and database, `503` if either fails) |
| `POST` | `/api/v1/runs/{id}/approve` | Resume the interrupted graph |
| `POST` | `/api/v1/runs/{id}/revise` | Request revision (bounded by `max_revision_rounds`) |
| `GET` | `/api/v1/campaigns/{id}` | Final package with evidence |
| `GET` | `/api/v1/me` | The signed-in caller and their organisations, with roles |
| `POST` | `/api/v1/organisations` | Create an organisation; the caller becomes owner (`app.create_organisation`, 0012) |
| `CRUD` | `/api/v1/brands`, `/products`, `/products/{id}/claims` | Setup. Built in Phase 1: list, create and delete brands and products, each with an explicit `org_id`; claims later |
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
`migration list` must show local and remote identical after every push. The full database suite
passes on CI's pgvector image before a push, never after.

**Before pushing a migration that creates or changes an `app.*` function** (the lesson above):
run the migration file against the project inside a transaction that is always rolled back
(`psycopg`: `async with conn.transaction(force_rollback=True)`), and inside it read, for every
such function, its owner, `prosecdef`, `proconfig` and
`has_function_privilege(<role>, <function>, 'EXECUTE')` for `anon`, `authenticated` and
`service_role`. They must match what `backend/tests/db/test_table_posture.py` states. Then
confirm nothing persisted (for example, a role the migration creates does not exist after the
rollback). DDL, `CREATE ROLE` included, is transactional, so the dry run leaves no trace. Done for
0012 on 2026-10-16; after the push, the live posture was read again and matched.

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
  created once with the secret key by `scripts/ensure_e2e_users.py`). Permanent footprint: 2
  users and 2 organisations; each run deletes the brand and product it creates. In CI, which
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
