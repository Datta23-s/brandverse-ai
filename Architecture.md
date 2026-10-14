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
│ 3. API GATEWAY — FastAPI (Render)                                │
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

| Tier | Model (Oct 2026) | Used for | Approx input / output per 1M |
|---|---|---|---|
| **Low-cost** | `gemini-3.1-flash-lite` | Classification, summarisation, query rewriting, metadata extraction, relevance pre-filter | $0.25 / $1.50 |
| **Standard** | `gemini-3-flash-preview` | Product intelligence, persona analysis, campaign planning, most agent work | $0.50 / $3.00 |
| **Strong reasoning** | `gemini-3.1-pro-preview` | Conflicting evidence, high-risk claims, final strategy arbitration | $2.00 / $12.00 |
| **Dedicated media** | `gemini-3.1-flash-image` (stable; the `-preview` tag shut down 25 Jun 2026) | Posters, social visuals | $0.067 / 1K images |
| **Fallback** | OSS model via LiteLLM (Hugging Face / local) | Provider outage, budget exhaustion | — |

### 4.2 Routing policy

Route on five signals, in this order of precedence:

```python
def route(task) -> Tier:
    if task.risk == "high" or task.has_conflicting_evidence:
        return Tier.STRONG            # correctness outranks cost
    if task.kind in MEDIA_KINDS:
        return Tier.MEDIA
    if task.complexity == "trivial" or task.is_structural:
        return Tier.LOW
    if budget.remaining_usd < budget.reserve_floor:
        return Tier.LOW               # degrade rather than abort
    if task.prior_failures >= 1:
        return escalate(task.last_tier)
    return Tier.STANDARD
```

Every routing decision is persisted to `llm_calls` with its reason. The evaluation harness reads that table directly.

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
| **A2** | Market & Advertisement Intelligence | Competitor ads, category conventions, whitespace | Standard | 9 |
| **A3** | Customer Behaviour Intelligence | Personas, jobs-to-be-done, objections, channel affinity | Standard | 6 |
| **A4** | Sector & Evidence Intelligence | Regulatory constraints, required disclaimers, sourced proof | Strong | 9 |
| **A5** | Growth, Pricing & Offer Intelligence | Offer architecture, price framing, incentive design | Standard | 9 |
| **A6** | Creative Campaign Intelligence | Copy, headlines, hooks, scripts, storyboards, poster prompts | Standard + Media | 6 |
| **A7** | Trust, Risk & Campaign Evaluation | Claim verification, risk flags, brand-safety score | Strong | 6 |

**Adaptive selection.** A1, A6 and A7 always run — they are the floor (know the product, make the thing, check the thing). A2–A5 are selected by request type, sector risk and memory coverage. A run therefore uses **3 to 7 agents**, and the selection rationale is persisted for the evaluation.

**Failure policy.** An agent failure is recorded in `errors` and the run continues with a declared gap. One failing agent must never abort a campaign.

---

## 8. Validation engine

Three independent gates. Any `block` stops publication.

| Gate | Checks | Outcome |
|---|---|---|
| **Claim** | Every factual assertion maps to a `verified` claim or a scored source | `pass` / `flag` / `block` |
| **Brand voice** | Cosine distance between generated copy embedding and brand voice vector | `pass` if above threshold |
| **Risk** | Restricted claims, regulated-sector language, superlatives without proof, 8 predatory marketing anti-patterns | `pass` / `block` |

The anti-pattern blacklist (from AMAD-MS gap G6): false scarcity, fabricated social proof, undisclosed paid endorsement, vulnerability targeting, dark-pattern consent, unsubstantiated health claims, price anchoring on fictional prices, confirmshaming.

---

## 9. Data model

Abbreviated DDL. **`docs/erd.md` is canonical** — it records why each column and constraint exists
(issues E1–E7). This section must match it; change both in the same commit. Full migrations live in
`supabase/migrations/`.

**Tenant consistency pattern.** Every tenant table carries its own `org_id` so RLS is a direct
column check, never a join. A child's `org_id` is forced to equal its parent's by a composite
foreign key `(parent_id, org_id) → parent (id, org_id)`, so a row can never point across tenants.
Composite `on delete set null (col)` requires **PostgreSQL 15+** — confirm the Supabase project
version before the first migration.

```sql
create extension if not exists vector;
create extension if not exists pg_trgm;

create table organisations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  budget_cap_usd numeric(10,2) default 50.00,
  created_at timestamptz default now()
);

create table brands (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  name text not null,
  voice_guidelines text,
  voice_embedding vector(768),
  voice_embedding_model text,                                     -- E3
  voice_embedding_version text,                                   -- E3
  sector text,
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

-- Every call. The evaluation harness reads this table.
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
  sector text,
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

create index on memory_items using hnsw (embedding vector_cosine_ops);
create index on memory_items using gin (to_tsvector('english', content));

create table campaigns (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  run_id uuid not null,
  strategy jsonb, creative jsonb, validation jsonb,
  status text default 'draft'
    check (status in ('draft','awaiting_approval','approved','rejected')),
  approved_by uuid, approved_at timestamptz,
  quality_scores jsonb,
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade
);
```

Nullable FK columns (`runs.product_id`, `memory_items.brand_id / product_id / claim_id`) use the
default `MATCH SIMPLE`: when the reference column is null the constraint is not checked, which is
the intended behaviour for org-wide items.

**Row-level security is mandatory on every table carrying `org_id`** — every table above except
`organisations`. Write the policy in the same migration that creates the table, never in a
later one. Every policy is a direct column check; join-based policies are not permitted.

```sql
alter table brands enable row level security;
create policy brand_tenant_isolation on brands
  using (org_id = (auth.jwt() ->> 'org_id')::uuid);
```

---

## 10. API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs` | Start a campaign run → `202` + `run_id` |
| `GET` | `/api/v1/runs/{id}` | Status, current step, budget consumed |
| `GET` | `/api/v1/runs/{id}/stream` | SSE step-by-step progress for the live pipeline UI |
| `POST` | `/api/v1/runs/{id}/approve` | Resume the interrupted graph |
| `POST` | `/api/v1/runs/{id}/revise` | Request revision (bounded by `max_revision_rounds`) |
| `GET` | `/api/v1/campaigns/{id}` | Final package with evidence |
| `CRUD` | `/api/v1/brands`, `/products`, `/products/{id}/claims` | Setup |
| `GET` | `/api/v1/memory/search` | Debug and demo view of retrieval with `M` breakdown |
| `POST` | `/api/v1/experiments/run` | Execute an evaluation arm |
| `GET` | `/api/v1/experiments/report` | ARR, SRR, CR, LR |

The SSE stream is what makes the 15-step pipeline visible in the demo. **Build it in Phase 1, not Phase 11** — it is the single highest-value demo asset in the project.

---

## 11. Deployment

| Component | Host | Notes |
|---|---|---|
| Frontend | Vercel | Preview deploy per PR |
| API + worker | Render | Two services, one image |
| Postgres + pgvector | Supabase | Free tier sufficient for the thesis |
| Redis | Render Redis / Upstash | Queue + zero-retention research cache |
| SearXNG | Render Docker or a small VPS | Must be self-hosted; public instances rate-limit |
| Secrets | Platform env vars | Never in the repo. See `Rules.md`. |

**Environment variables**

```
SUPABASE_URL=                 SUPABASE_SERVICE_KEY=
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
