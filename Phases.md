# BrandverseAI — Implementation Phases

| Field | Value |
|---|---|
| Document | Phases.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Depends on | `PRD.md`, `Architecture.md`, `Rules.md` |

---

## 0. Two tracks

The original roadmap is 20 weeks for a full team. Most B.Tech teams have less time and fewer hands. Two tracks are defined. **Pick one now and say which one you picked.**

| Track | Duration | Agents | For |
|---|---|---|---|
| **Full** | 20 weeks | 7 | Full team, full academic year |
| **Thesis-critical** | 10 weeks | 5 | Realistic single-semester delivery |

The thesis-critical track ships everything the *finding* depends on and defers everything else. It is not a lesser project — it is the same contribution with less surface area. **A working 5-agent system beats a broken 7-agent one in every viva ever held.**

### 0.1 Thesis-critical path

```
P0 → P1 → P2 → P3 → P4 → P5 → P6(5 agents) → P7 → P10 → P11
```

Deferred: P8 Creative Studio (beyond basic copy), P9 Enterprise Capabilities (A4 and A5 agents, the persistent competitor advertisement repository), P12 Monitoring.

**Never defer:** P4 Memory, P5 Research, P7 Adaptive Intelligence, P10 Evaluation. Those four *are* the thesis.

---

## Phase 0 — Requirements & Architecture
**Week 1 · Full and thesis-critical**

| Deliverable | Done when |
|---|---|
| This document set | All six files committed and reviewed |
| Repository scaffold | Monorepo, CI green on an empty build |
| Data model draft | ERD matching `Architecture.md` §9 |
| Evaluation framework defined | Metrics, arms and test-set design agreed in writing |
| Model IDs verified | `config/models.yaml` populated from live provider docs |

**Exit criteria**
- [ ] Every team member can explain the 15-step pipeline without notes
- [ ] `PRD.md` §1.2 departures agreed and rehearsed
- [ ] Gemini 3.x IDs, prices and embedding dimensionality confirmed against `ai.google.dev` (re-check before every deploy — providers move these without notice)
- [ ] Test-set design includes repeat requests per brand

---

## Phase 1 — Platform Foundation
**Weeks 2–3**

| Deliverable | Done when |
|---|---|
| Next.js 15 app, running locally | Runs on the laptop against the local API (amended 2026-10-16; public deployment is Phase 11 — see below) |
| FastAPI service, deployed | `/health` returns 200 on the laptop (worker-host decision, `Architecture.md` §11; Render free has no workers) |
| Supabase project | Auth working, RLS enabled on every tenant table |
| Core schema | Full `Architecture.md` §9 schema migrated (`supabase/migrations/0001`–`0008`), including `memory_items` with HNSW + GIN — pulled forward from Phase 4 so the RLS test covers every tenant table from the start |
| Database test (merge-blocking) | `backend/tests/db/` passes in CI against `pgvector/pgvector:pg17` with the real migrations; required status check |
| Redis + ARQ worker | A trivial job enqueues and completes |
| **SSE run-progress stream** | A fake 15-step run renders live in the UI |

**Exit criteria**
- [ ] Sign up → create brand → create product works end to end — **un-ticked 2026-10-17:
  not observed in full.** Observed on the live project: the owner's sign-up through the form,
  email confirmed through the real confirmation email, sign-in, and organisation creation
  (`POST /api/v1/organisations` → 201 at 05:09:05 UTC, via `app.create_organisation`); then a
  fake run in that organisation. **Not observed:** a brand or a product. The live API's access log
  from 05:04:17 UTC on holds no brand or product request from anyone, and the project holds 0
  brands and 0 products (`pg_stat_user_tables` counts only the E2E runs' inserts and deletes).
  The brand → product steps are covered on the live project by
  `frontend/e2e/auth-and-setup.spec.ts` (fixed E2E users), but the criterion is ticked only
  when the owner's own brand and product are seen in the database.
- [x] Cross-tenant access test written and green — `backend/tests/db/test_rls.py` (every tenant
  table) and `backend/tests/db/test_api.py` (a member of org A cannot stream, read or write
  org B's runs, brands or products; signed-out requests are refused), merge-blocking in CI.
- [x] The fake pipeline animates in the browser — `frontend/e2e/run-view.spec.ts` samples the
  stepper through a live run (each step seen running in order, ≥10 steps, earlier done and later
  waiting), in Chromium against the real API, worker and Redis, in CI on every push (2026-10-17).

> Building the SSE stream in Phase 1 against a fake run is the highest-leverage decision in this plan. It gives you a demo from week 3 and a debugging surface for everything after.

**Amended 2026-10-16 — no public Vercel URL in Phase 1, stated plainly.** The original deliverable
was "Public URL live on Vercel". It is not achievable in this phase: the API, the ARQ worker and
Redis run on the developer's laptop (`Architecture.md` §11, worker-host decision), and a
Vercel-hosted frontend cannot reach them without a tunnel exposing the laptop's services, which
this project does not build. The run view needs the API for every request and the SSE stream, so
a public frontend would have nothing to talk to. In Phase 1 the Next.js app therefore runs
locally against the local API. The public deployment — the frontend on Vercel serving a seeded,
read-only demo path — is Phase 11 work, amended the same way and for the same reason (Phase 11,
"Limitation, stated plainly").

---

## Phase 2 — LLM & Structured Intelligence
**Week 4**

| Deliverable | Done when |
|---|---|
| LiteLLM gateway | All model calls route through it |
| `config/models.yaml` | Three free-tier tiers (cheap, standard, strong) plus embedding, pinned with their RPM/TPM/RPD limits (`Architecture.md` §4.1) |
| Structured output | Pydantic schema enforced on every completion |
| `llm_calls` logging | Model, tier, tokens, cost, latency recorded per call |
| Prompt management | Versioned prompt templates, not inline f-strings |
| Budget pre-flight check | A call that would breach the cap is refused |

**Exit criteria**
- [ ] A deliberate over-budget run aborts cleanly with partial output
- [ ] Cost per call appears in the database within one second
- [ ] Switching a tier's model is a one-line config change

---

## Phase 3 — Product Truth & Claims
**Week 5**

| Deliverable | Done when |
|---|---|
| `product_claims` CRUD | Human can add verified claims with evidence |
| Claim classifier | Generated statements labelled verified / deduced / restricted |
| Evidence storage | Source URL and note persisted per claim |
| Claim validation gate | Unsourced hard claims blocked |
| Sector constraint registry enforcement | Per-sector required disclaimers and prohibited phrasings enforced by rule in all three gates (`Architecture.md` §8) |

**Exit criteria**
- [ ] A product with no verified claims cannot start a run
- [ ] An attempt to generate a restricted claim is blocked and logged
- [ ] Every claim in a sample output resolves to a source
- [ ] Output in a regulated sector missing a required disclaimer is blocked
- [ ] Output containing a registry-prohibited phrasing is blocked
- [ ] A regulated sector with no active registry entries blocks the run (fails closed)

---

## Phase 4 — Marketing Memory
**Weeks 6–7 · Never defer**

| Deliverable | Done when |
|---|---|
| `memory_items` + HNSW + GIN | Migrated with RLS — **delivered in Phase 1** (migration 0006) |
| Embedding pipeline | Chunk, embed, store, with model version recorded |
| `match_memory` RRF function | Deployed and unit-tested |
| `M`-score calculator | Returns the per-component breakdown |
| Reuse bands | 0.90 / 0.80 / 0.65 thresholds wired |
| Freshness decay | Applied at scoring time |
| Write-back, approval-gated | Only approved facts persist |
| **50-query relevance set** | Hand-labelled and committed |
| Seed data | 3 brands, flagged and excluded from the harness |

**Exit criteria**
- [ ] Precision@10 measured on the relevance set
- [ ] Cross-tenant memory leak test green
- [ ] Near-duplicate detection prevents a second insert at cosine > 0.95
- [ ] Revocation removes an item from retrieval immediately

**pgvector version gap (added 2026-10-14).** CI's database gate runs pgvector 0.8.7; the
Supabase project runs 0.8.2 (`Architecture.md` §11). Once `memory_items` holds real data:
- [ ] Re-run the storage measurement against the real project (heap, TOAST, HNSW, GIN per row) and
  record it beside the CI figures, with the pgvector version and date
- [ ] Re-verify HNSW behaviour on the real project: the index is used by `match_memory`'s vector
  branch (`explain`), and recall at `ef_search = 100` matches CI's on the same labelled queries
- [ ] Either pin CI to production's pgvector version or record why not

---

## Phase 5 — Research Intelligence
**Weeks 8–9 · Never defer**

| Deliverable | Done when |
|---|---|
| SearXNG self-hosted | On the developer's laptop in Docker (decided 2026-10-14, `Architecture.md` §11); JSON API reachable from the worker |
| Search abstraction | One interface; SearXNG and the offline CI mock implement it. No paid SERP — the project spends nothing (`Rules.md` C0) |
| Trafilatura extraction | Main content extracted, boilerplate stripped |
| Source scoring | Authority, recency, specificity, corroboration |
| Provenance capture | Every finding carries its URL and score |
| Zero-retention cache | Raw bodies in Redis with TTL only |
| Query planner | Gaps converted into targeted queries |

**Exit criteria**
- [ ] 20 searches complete within the latency budget
- [ ] Low-scored sources are discarded before reaching an agent
- [ ] No raw page body exists in Postgres after a run
- [ ] Rate-limit failures degrade to partial results, not a crash

---

## Phase 6 — Agent MVP
**Weeks 10–11 · Thesis-critical ships 5**

| Agent | Full track | Thesis-critical |
|---|---|---|
| A1 Product & Brand | Phase 6 | **Phase 6** |
| A2 Market & Advertisement | Phase 6 | **Phase 6** *(live competitor research only — see 6.1)* |
| A3 Customer Behaviour | Phase 6 | **Phase 6** |
| A6 Creative Campaign | Phase 6 | **Phase 6** |
| A7 Trust & Risk | Phase 6 | **Phase 6** |
| A4 Sector & Evidence | Phase 9 | Deferred to Phase 9 |
| A5 Growth & Pricing | Phase 9 | Deferred to Phase 9 |

### 6.1 Why this agent set (decided 2026-10-14)

An earlier draft of this table pulled A4 forward into Phase 6 and deferred A2. That was resolved
in favour of the original design diagram's five-agent MVP: **A1, A2, A3, A6, A7**.

- **A4's safety-critical half is not an agent.** Required disclaimers and prohibited phrasings for
  regulated sectors are deterministic: they come from regulation, not from generation. They are
  held in the sector constraint registry inside the Validation Engine (`Architecture.md` §8),
  maintained by humans, enforced by rule, never LLM-generated, and built in Phase 3. Regulated-
  sector safety therefore does not depend on an agent shipping, and `Rules.md` T4 makes the
  registry the hard requirement. A4 as a research agent (sourced proof, evolving regulatory
  context) stays in Phase 9 as drawn.
- **A2 is split.** In Phase 6, A2 performs live competitor research through the Phase 5 research
  layer, which is already on the thesis-critical path. The **persistent competitor advertisement
  repository with change tracking stays in Phase 9**, and Phase 9's exit criteria own it.
- The regulated sector stays in the evaluation set, covered by the registry rather than by A4.

| Deliverable | Done when |
|---|---|
| LangGraph graph | All 15 nodes wired, Postgres checkpointer |
| Parallel fan-out | Agents run concurrently with reducers on shared keys |
| Human approval interrupt | Graph suspends and resumes from checkpoint |
| Agent failure isolation | One failing agent does not abort the run |

**Exit criteria**
- [ ] A real end-to-end campaign is produced and approved
- [ ] A killed worker mid-run resumes from its checkpoint
- [ ] Forcing an agent to fail still yields a campaign with a declared gap

---

## Phase 7 — Adaptive Intelligence
**Weeks 12–13 · Never defer — this is the contribution**

| Deliverable | Done when |
|---|---|
| Research Necessity Engine | `R` computed, D0/D1/D2 selected and logged |
| Dynamic agent selection | 3–7 agents chosen with persisted rationale |
| Model routing policy | Tier chosen per call with recorded reason |
| Budget manager | Per-run caps enforced across the whole run (`Rules.md` C3), plus the per-day per-model RPD budget with downgrade or deferral and a logged reason (C9) |
| Rate-limit scheduler | Per-model RPM/TPM buckets; `429` waits logged and kept out of the C8 retry count (C10) |

**Exit criteria**
- [ ] Identical request twice: second run picks a lower depth, logs prove it
- [ ] Agent count varies across request types
- [ ] Routing distribution is visible in the admin view
- [ ] A high-risk claim demonstrably escalates to the strong tier
- [ ] In a regulated-sector run, every claim-verification and risk-assessment call on that run's output runs on the strong tier, whichever agent performs it, and every other call keeps its default tier
- [ ] In a non-regulated run with no high-risk claim or evidence conflict, no call runs on the strong tier
- [ ] With the strong tier's RPD exhausted, an ordinary call is downgraded with a logged reason and a T4 call is deferred, never downgraded
- [ ] A burst of parallel agent calls queues within RPM limits; `429` waits are logged and do not count as retries

---

## Phase 8 — Creative Studio
**Week 14 · Deferred in thesis-critical**

Poster prompts, social ad formats, cartoon concepts, scripts, storyboards. Honours `max_media_generations = 4`.

**Exit criteria**
- [ ] Generated images render in the campaign report — **not achievable at zero cost**: no image model is on the free tier (`Architecture.md` §4.1). Poster prompts are delivered as text
- [ ] Media cap enforced

---

## Phase 9 — Enterprise Capabilities
**Weeks 15–16 · Deferred in thesis-critical**

A4 (Sector & Evidence research agent), A5, the persistent competitor advertisement repository with
change tracking, deep research mode, multi-country intelligence. A2's live competitor research
ships in Phase 6 (see 6.1); the repository is what Phase 9 adds to it.

**Exit criteria**
- [ ] Competitor advertisement repository populated, queryable and tracking changes over time
- [ ] A4 runs alongside the sector constraint registry; the registry remains the hard gate (`Rules.md` T4)
- [ ] Offer architecture appears in generated strategy

---

## Phase 10 — Testing & Research Evaluation
**Weeks 17–18 · Never defer — this is the dissertation**

| Deliverable | Done when |
|---|---|
| Unit, integration, security tests | Suite green offline in CI |
| **Baseline A harness** | Single LLM, no memory, no research |
| **Baseline B harness** | Fixed 7-agent, always-full research |
| **Experiment runner** | 30 requests × 3 arms, raw logs retained |
| ARR / SRR / CR / LR | Computed with mean, SD, significance tests |
| Quality rubric scoring | ≥ 2 human raters on ≥ 30 campaigns, Cohen's κ reported |
| Weight tuning | `R` and `M` weights tuned; defaults and tuned values both reported |
| Cost analysis | Tokens per approved campaign per arm (primary, by tier), with list-price-equivalent USD labelled as such — actual spend is zero |
| **Hash chain on `approvals` and `audit_log`** | Built and verified **before calibration begins** (`Architecture.md` §9, decision 2): tamper evidence for the decisions the results rest on — detection, not prevention |

**Exit criteria**
- [ ] Every reported number traces to a committed raw log
- [ ] Failed and capped runs included in the report
- [ ] Seed memory provably excluded
- [ ] Inter-rater agreement reported, not just a mean score

> Budget two full weeks. Teams that compress evaluation into the final weekend produce numbers they cannot defend.

**Execution time at free-tier limits (re-planned 2026-10-14, after A7 moved to Standard).**
Executing the 90 evaluation runs (30 requests × 3 arms) takes **about 1 week of calendar time**,
so the two weeks above are **not** analysis time: about one week executes, about one analyses.
The estimate, from the limits in `config/models.yaml`:

| Quota | Demand | Days of quota |
|---|---|---|
| Strong, 20/day | Only T4 calls (claim verification and risk assessment on regulated-sector output, by function) and high-risk calls (`Architecture.md` §4.2). A4 is deferred to Phase 9 on this track; if it is pulled forward, its regulated-sector verification calls add to this row. Proposed arm: the regulated brand's 3 requests (R19–R21) and the 3 with entered evidence conflicts (R01–R03), at most about 3 Strong calls each, so ≤ 18. Baselines A and B: 0. Tuning set: 1 regulated request. | About 1 — no longer binding |
| Standard + Cheap, 500/day each | Baseline A 30 calls; Baseline B ≤ 600 (20 × 30, all Standard); Proposed ≤ 600 across Cheap and Standard. Worst case about 1,230 on Standard alone. | About 3 |
| RPM (5 / 15 per minute) | About 1,250 calls | Under 2 hours of pure rate — not binding |
| Embedding, 1000/day | Memory retrieval and write-back: tens per run. Research chunks: **depends on how batch requests are counted** (below). | Under 1 if batched; see below |

The schedule is then set by **approval rounds**, not quota: in the Proposed arm a brand's campaign
N+1 is generated only after campaign N is decided, so there are at least three generate → approve
rounds, each needing an approval sitting (≤ 15 campaigns per sitting, rubric §2; 90 campaigns is at
least 6 sittings).

**Open risk — research-chunk embeddings.** Whether a request embedding several texts (one
`Content` object each) counts as one request or as one per text against the 1000/day limit is not
stated in Google's docs (checked 2026-10-14). If per text, a deep-research run embedding about 800
chunks uses most of a day's quota, and Baseline B's 30 always-deep runs alone would take about a
month. Verify in Phase 5 by sending one multi-text request and reading the AI Studio usage
counter. If it counts per text, embed only the top-k chunks after a lexical pre-filter (about 50 per
run: about 2,000 requests in total, about 2 days).

Start executing at the beginning of Phase 10, or as soon as Phase 7 is frozen. Measure Strong
calls per run on the tuning set first, and re-plan from the measured figure.

---

## Phase 11 — Production Deployment
**Week 19**

Vercel (frontend), Supabase production config, the laptop stack (API, worker, Redis, SearXNG —
`Architecture.md` §11). Secrets in platform env vars and a git-ignored local `.env`. Seeded demo
org. Free tiers only (`Rules.md` C0).

**Limitation, stated plainly.** On a zero-cost stack the worker, Redis and SearXNG run on the
developer's laptop, and no free cloud host can reach them without exposing them. A stranger on a
clean device therefore **cannot run the live pipeline from the public URL**. The public URL serves
the frontend with a seeded, read-only demo path (a real campaign produced earlier, with its
evidence and logs); the live pipeline is demonstrated from the laptop.

**Supabase free-tier auto-pause:** the project is paused after 1 week of inactivity. Touch the
database at least weekly — a real query or API call against the project — for the whole 20 weeks,
not only at deployment. If it does pause, restore it from the Supabase dashboard (*Restore
project*; same URL and keys) within the restore window — plan on 90 days — and otherwise restore
the downloaded backup into a new project and update the URL and keys (`Architecture.md` §11).
The free tier has no backups: `pg_dump` weekly and after every evaluation batch.

**Exit criteria**
- [ ] A clean device can open the public URL and walk the seeded demo path end to end (a completed campaign with evidence, approval and cost logs), with no laptop service running
- [ ] The live pipeline runs end to end from the laptop, and that run is the one in the recorded fallback video
- [ ] No secret exists in git history
- [ ] A rehearsed 8-minute demo script exists with a recorded fallback video

---

## Phase 12 — Monitoring & Optimisation
**Week 20 onward · Deferred in thesis-critical**

Cost, latency, retrieval quality, failure rates, model usage, approvals, feedback.

---

## Milestone summary

| Week | Milestone | Demo-able |
|---|---|---|
| 1 | Docs and scaffold | — |
| 3 | Platform + live fake pipeline | **Yes** |
| 5 | Product truth enforced | Yes |
| 7 | Memory retrieving with scores | **Yes — the core** |
| 9 | Live research with provenance | Yes |
| 11 | First real end-to-end campaign | **Yes — the headline** |
| 13 | Adaptivity measurable | **Yes — the contribution** |
| 18 | Results with significance | **Yes — the thesis** |
| 19 | Public deployment | Yes |

---

## Risk checkpoints

| End of | Ask | If no |
|---|---|---|
| Every week, weeks 1–20 | Has the Supabase project been touched in the last 7 days? | It pauses. Restore it from the dashboard (*Restore project*) the same day and take a `pg_dump`; see Phase 11 |
| Week 3 | Does the fake pipeline render live? | Stop; fix before adding logic |
| Week 7 | Does memory retrieve relevant items at Precision@10 > 0.7? | Stop; the thesis depends on it |
| Week 11 | Has a real campaign been approved end to end? | Cut to the thesis-critical track now |
| Week 13 | Does the second run for a brand cost less than the first? | **Stop everything.** This is the finding. If it is not true, find out why before building anything else. |
