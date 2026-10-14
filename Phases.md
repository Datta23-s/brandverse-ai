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

Deferred: P8 Creative Studio (beyond basic copy), P9 Enterprise Capabilities (A2, A4, A5), P12 Monitoring.

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
- [ ] Gemini 3.x IDs confirmed against `ai.google.dev` *(2.5 retires 16 Oct 2026)*
- [ ] Test-set design includes repeat requests per brand

---

## Phase 1 — Platform Foundation
**Weeks 2–3**

| Deliverable | Done when |
|---|---|
| Next.js 15 app, deployed | Public URL live on Vercel |
| FastAPI service, deployed | `/health` returns 200 on Render |
| Supabase project | Auth working, RLS enabled on every tenant table |
| Core schema | organisations, brands, products, product_claims migrated |
| Redis + ARQ worker | A trivial job enqueues and completes |
| **SSE run-progress stream** | A fake 15-step run renders live in the UI |

**Exit criteria**
- [ ] Sign up → create brand → create product works end to end
- [ ] Cross-tenant access test written and green
- [ ] The fake pipeline animates in the browser

> Building the SSE stream in Phase 1 against a fake run is the highest-leverage decision in this plan. It gives you a demo from week 3 and a debugging surface for everything after.

---

## Phase 2 — LLM & Structured Intelligence
**Week 4**

| Deliverable | Done when |
|---|---|
| LiteLLM gateway | All model calls route through it |
| `config/models.yaml` | Four tiers defined with pinned Gemini 3.x IDs |
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

**Exit criteria**
- [ ] A product with no verified claims cannot start a run
- [ ] An attempt to generate a restricted claim is blocked and logged
- [ ] Every claim in a sample output resolves to a source

---

## Phase 4 — Marketing Memory
**Weeks 6–7 · Never defer**

| Deliverable | Done when |
|---|---|
| `memory_items` + HNSW + GIN | Migrated with RLS |
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

---

## Phase 5 — Research Intelligence
**Weeks 8–9 · Never defer**

| Deliverable | Done when |
|---|---|
| SearXNG self-hosted | JSON API reachable from the worker |
| Search abstraction | One interface; SearXNG and a paid SERP both implement it |
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
| A3 Customer Behaviour | Phase 6 | **Phase 6** |
| A6 Creative Campaign | Phase 6 | **Phase 6** |
| A7 Trust & Risk | Phase 6 | **Phase 6** |
| A4 Sector & Evidence | Phase 9 | **Phase 6** *(pulled forward — regulated-sector safety)* |
| A2 Market & Advertisement | Phase 9 | Deferred |
| A5 Growth & Pricing | Phase 9 | Deferred |

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
| Budget manager | Caps enforced across the whole run |

**Exit criteria**
- [ ] Identical request twice: second run picks a lower depth, logs prove it
- [ ] Agent count varies across request types
- [ ] Routing distribution is visible in the admin view
- [ ] A high-risk claim demonstrably escalates to the strong tier

---

## Phase 8 — Creative Studio
**Week 14 · Deferred in thesis-critical**

Poster prompts, social ad formats, cartoon concepts, scripts, storyboards. Honours `max_media_generations = 4`.

**Exit criteria**
- [ ] Generated images render in the campaign report
- [ ] Media cap enforced

---

## Phase 9 — Enterprise Capabilities
**Weeks 15–16 · Deferred in thesis-critical**

A2, A5, deep research mode, multi-country intelligence.

**Exit criteria**
- [ ] Competitor advertisement repository populated and queryable
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
| Cost analysis | USD per approved campaign per arm |

**Exit criteria**
- [ ] Every reported number traces to a committed raw log
- [ ] Failed and capped runs included in the report
- [ ] Seed memory provably excluded
- [ ] Inter-rater agreement reported, not just a mean score

> Budget two full weeks. Teams that compress evaluation into the final weekend produce numbers they cannot defend.

---

## Phase 11 — Production Deployment
**Week 19**

Vercel, Render, Redis, Supabase production config. Secrets in platform env vars. Seeded demo org.

**Exit criteria**
- [ ] A clean device can run the full demo on the public URL
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
| Week 3 | Does the fake pipeline render live? | Stop; fix before adding logic |
| Week 7 | Does memory retrieve relevant items at Precision@10 > 0.7? | Stop; the thesis depends on it |
| Week 11 | Has a real campaign been approved end to end? | Cut to the thesis-critical track now |
| Week 13 | Does the second run for a brand cost less than the first? | **Stop everything.** This is the finding. If it is not true, find out why before building anything else. |
