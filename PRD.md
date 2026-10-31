# BrandverseAI — Product Requirements Document

**Adaptive Product-Personalised Agentic AI Marketing Intelligence Platform**

| Field | Value |
|---|---|
| Document | PRD.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Status | Baseline for implementation |
| Academic grounding | *Autonomous Multi-Agent Architecture for Enterprise Marketing Decision Systems* (AMAD-MS), Sambare et al., PCCOE Pune |
| Programme | B.Tech final year project, Department of IT / Computer Engineering |

---

## 0. How to read this document

This PRD is written to be **executed by an AI coding agent**, not only read by a human. Every requirement is numbered, testable, and traceable to a research gap. When an instruction is ambiguous, the agent must stop and ask rather than guess. Companion documents:

- `Architecture.md` — how the system is built
- `Memory.md` — how the system remembers
- `Rules.md` — what the coding agent must and must not do
- `Phases.md` — the order of construction
- `Design.md` — how the product looks and feels

---

## 1. The reconciliation: AMAD-MS and BrandverseAI

**Read this section first. It resolves the single biggest inconsistency in the project.**

The published research paper describes **AMAD-MS**, a five-layer architecture. The system diagrams describe **BrandverseAI**, an eleven-layer agentic platform. These are not the same system, and an examiner will notice. The project's official position is:

> **AMAD-MS is the theoretical contribution. BrandverseAI is its engineering realisation.** The paper establishes *what must be true* of an autonomous marketing decision system. The platform implements those requirements with a modern agentic stack, and deliberately departs from the paper where the paper's assumptions did not survive contact with implementation.

### 1.1 Mapping table — paper to platform

| AMAD-MS layer (paper) | BrandverseAI realisation | Status |
|---|---|---|
| L1 Heterogeneous Ingestion & Federation | Layer 6b Research Layer + product/brand ingestion into Supabase | Implemented, narrowed |
| L2 Anonymized Privacy Boundary | Trust & Risk Agent (A7) + PII tokenisation at ingest + zero-retention research cache | Implemented, reduced |
| L3 Dual Engine Analytics Core (RF + GenAI) | LLM Layer (6c) + Memory Layer (6a). **Random Forest replaced by retrieval-scored memory reuse.** | **Deliberate departure** |
| L4 Multi-Objective Decision Arbitration | Research Necessity Engine + Model Routing Policy + Cost Governance | Implemented, reframed |
| L5 Automated Execution & Telemetry | Campaign Intelligence + Reports/Creative/Analytics + Performance Feedback loop | Implemented, no live ad-network execution |

### 1.2 Declared departures (defend these, do not hide them)

| # | Departure | Honest reason |
|---|---|---|
| D1 | No Random Forest conversion classifier | The paper's RF engine requires 120,000 labelled transaction records with 34 behavioural features. No such first-party dataset exists for this project. Substituting a model trained on synthetic data would be a fabricated result. Memory relevance scoring (`M`) performs the analogous "is this worth acting on" function with real, auditable inputs. |
| D2 | No live Meta / Google Ads API execution | Requires a funded advertiser account and platform review. Out of scope for an academic build. The execution layer produces an approved, export-ready campaign package instead. |
| D3 | Differential privacy (ε=0.5) not implemented as stated | DP noise is meaningful only over a population dataset. BrandverseAI operates per-brand on first-party product data. Implemented instead: PII tokenisation, zero-retention research cache, and explicit consent-scoped memory. |
| D4 | Added: adaptive research depth, model routing, cost governance | These are the project's **original contribution beyond the paper** and the basis of the experimental comparison in §8. |

> **Panel answer, memorised:** "The paper proves the need. The platform proves the build. Three things in the paper did not survive implementation, and I can tell you exactly why for each."

---

## 2. Problem statement

### 2.1 Evidence base (from the paper's empirical corpus, N=58 analysts + N=9 executives, 47-paper review)

| Finding | Value | Source |
|---|---|---|
| Analytical tools a marketer juggles | 16 on average | Kietzmann et al. [1] |
| Strategic time lost to manual data preparation | 60.0% | [1] |
| Organisations citing prohibitive cost as an AI barrier | 62% | [1] |
| Organisations citing technical illiteracy | 41% | [1] |
| Practitioners demanding process automation | 81.0% | [1] |
| Drop in repurchase intent under invasive personalisation | 28.5% | [7], [31] |
| Campaign planning latency, traditional | 14.2 days | [1], [11] |

### 2.2 The four failures BrandverseAI targets

**F1 — Fragmentation.** Marketing intelligence is scattered across sixteen tools that do not share state. Nothing learns from anything else.

**F2 — Amnesia.** Every AI marketing tool starts from zero on every request. The same brand, the same product, the same competitor landscape — researched again, paid for again. This is the central waste BrandverseAI attacks.

**F3 — Hallucinated claims.** LLMs invent product capabilities. In marketing this is not a quality defect, it is a legal and brand-equity liability. An ad that claims a feature the product lacks is actionable.

**F4 — Unbounded cost.** Agentic systems fan out without budget. A naive 7-agent system with uncapped research can spend tens of dollars on one campaign, which makes it unusable for the SME segment that most needs it.

### 2.3 Core principle

> **Retrieve existing intelligence → identify what is missing → research only the gap → personalise to the product → generate → validate against evidence → learn and reuse.**

Every design decision in this project defends that sentence. If a feature does not serve it, it is out of scope.

---

## 3. Users

| Persona | Role | Primary job | Success looks like |
|---|---|---|---|
| **Priya — SME marketer** | Owner-operator, 1-person marketing team | Launch a campaign for a product without a research agency | Evidence-backed campaign in under 10 minutes, under $2.50 |
| **Arjun — Agency strategist** | Manages 12 brands | Reuse brand intelligence across campaigns instead of re-researching | Second campaign for a known brand costs ~70% less than the first |
| **Meera — Brand manager** | Enterprise, regulated sector | Ensure nothing untrue or off-brand ships | Every claim traceable to a verified source; approval gate before publish |
| **Admin** | Platform operator | Control cost and access | Per-organisation budget caps, usage dashboards, audit log |

---

## 4. Scope

### 4.1 Must-have (M)

| ID | Requirement | Acceptance test |
|---|---|---|
| M1 | Organisation / brand / product hierarchy with authenticated multi-tenant isolation | User from Org A cannot read Org B's brand by any API path; verified by automated test |
| M2 | Product truth store separating **verified claims** from **AI deductions** | Every generated claim carries `claim_type ∈ {verified, deduced, restricted}` and a source id |
| M3 | Marketing memory with vector + keyword hybrid retrieval | Retrieval returns scored results with `M` ∈ [0,1] and the component breakdown |
| M4 | Research Necessity Engine producing D0 / D1 / D2 | Given identical input twice, second run selects a lower depth and logs why |
| M5 | Adaptive agent selection (3 to 7 of 7 agents) | Agent set varies by request type; selection rationale is persisted |
| M6 | Model routing across at least three cost tiers | Routing decision logged per call with model, reason, token count, cost |
| M7 | Cost governance hard caps enforced before execution | Run aborts cleanly at cap with partial results, never silently overspends |
| M8 | Campaign generation: strategy + creative + evidence pack | Output renders as a report with inline source citations |
| M9 | Human approval gate before a campaign is marked final | No campaign reaches `status=approved` without a recorded human action |
| M10 | Validated knowledge written back to memory | After approval, reusable facts are embedded and stored with provenance |
| M11 | Public marketing site with 3D hero | Loads under 3s on mid-range mobile, degrades gracefully without WebGL |
| M12 | Experimental harness comparing Baseline A / Baseline B / Proposed | Produces ARR, SRR, CR, LR with per-run raw logs |

### 4.2 Could-have (C)

| ID | Requirement | Business value |
|---|---|---|
| C1 | Multi-country / multi-language campaign variants | Market expansion |
| C2 | Image and poster generation | Creative completeness |
| C3 | Competitor advertisement repository with change tracking | Strategic differentiation |
| C4 | Scheduled re-research on stale memory | Freshness without user action |
| C5 | A/B variant performance ingestion from real channels | Closes the feedback loop with real data |
| C6 | Agentforce-style conversational interface over the memory | Accessibility for non-technical users |
| C7 | Shapley multi-touch attribution | Honours AMAD-MS gap G5 |
| C8 | Federated lookalike detection across brands | Paper's stated future work |

### 4.3 Out of scope (state this plainly in the viva)

- Live ad spend on Meta / Google / LinkedIn
- Payment processing or billing
- Real consumer PII at any point
- Training a foundation model
- Mobile native applications
- **Data export and deletion endpoints.** Required before any real deployment under India's
  Digital Personal Data Protection (DPDP) Act, 2023; deferred to post-thesis. The thesis build
  holds no real consumer PII (above).
- **DPDP erasure vs the append-only `audit_log` — conflict acknowledged.** A data principal's
  erasure right conflicts with an audit log that cannot be updated or deleted
  (`Architecture.md` §9). Intended resolution: **retain the audit event, and pseudonymise or
  tombstone the data subject** within it, with retention of audit records resting on an
  **independent legal basis**. This is a **deployment-phase decision requiring legal review**,
  not a schema change now; the append-only guarantee stays as built until that review concludes. That guarantee binds the application roles only; the table owner (`postgres`) can bypass it (`Architecture.md` §9, stated limitation).

---

## 5. Functional specification — the 15-step pipeline

Each step is a node in the orchestration graph. `Architecture.md` gives the implementation; this is the contract.

| # | Step | Input | Output | Fails how |
|---|---|---|---|---|
| 1 | User Requirement | Campaign brief, objective, audience hint, budget tier | Normalised `RunRequest` | Reject if no product selected |
| 2 | Product Context | `product_id` | Product record, attributes, assets, prior campaigns | Reject if product has no verified claims |
| 3 | Product Truth Validation | Product claims | `verified[]`, `deduced[]`, `restricted[]` | Blocks run if a restricted claim is requested |
| 4 | Marketing Memory Retrieval | Query embedding + filters | Ranked memory items with `M` scores | Degrades to empty set, never errors |
| 5 | Research Necessity Assessment | Memory coverage + request novelty | `R` score, depth ∈ {D0, D1, D2} | Defaults to D1 on scorer failure |
| 6 | Missing-Knowledge Research | Gap list, depth | Sourced findings with provenance | Honours `max_search_queries`; partial results allowed |
| 7 | Adaptive Agent Execution | Selected agent subset | Per-agent structured intelligence | Any agent may fail; run continues with recorded gap |
| 8 | Product-Customer Personalisation | Agent outputs + product truth | Persona-matched angles | — |
| 9 | Campaign Strategy | All above | Positioning, channels, messaging pillars | — |
| 10 | Creative Generation | Strategy | Ad copy, headlines, scripts, poster prompts | Honours `max_media_generations` |
| 11 | Evidence / Trust Validation | Every generated claim | Pass / flag / block per claim | Blocks publish on any unsourced hard claim |
| 12 | Human Approval | Validated package | Approved / rejected / revise, decided against `docs/approval-rubric.md` | `max_revision_rounds = 2` |
| 13 | Final Campaign Intelligence | Approved package | Report, assets, analytics | — |
| 14 | Performance Feedback | Optional real metrics | Outcome record | Optional |
| 15 | Validated Knowledge Memory | Approved facts | Embedded memory rows | Writes only verified or approved-deduced facts |

Step 15 feeds step 4 on the next run. **That loop is the product.**

---

## 6. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| N1 | End-to-end run latency, D0 path | < 30 s |
| N2 | End-to-end run latency, D2 path | < 5 min |
| N3 | Cost per run | Tokens are the primary unit; ≤ $2.50 list-price-equivalent hard cap as a safety limit. Actual spend is zero (free tiers only, `Rules.md` C0) |
| N4 | Landing page LCP, mid-range mobile | < 2.5 s |
| N5 | 3D hero frame rate, mid-range laptop | ≥ 50 fps |
| N6 | WebGL absent | Static fallback hero, full site usable |
| N7 | Tenant isolation | Enforced at the database row level |
| N8 | Every LLM call | Logged with model, tokens, cost, latency, purpose |
| N9 | Accessibility | WCAG 2.2 AA on all non-3D content |
| N10 | Reduced motion | `prefers-reduced-motion` halts all animation |

**N2 is contradicted by the free-tier search infrastructure — measured 2026-10-29.** A live D2 run issues the
planner's 8 searches (`Phases.md` Phase 5). The shortest gap shown sustainable for the declared engine set is 2
minutes, so the searches alone take about 16 minutes, against a 5-minute target for the whole run, and the target
cannot be met live whatever its search share was meant to be (shorter gaps are untested; a burst of about ten
searches in minutes got this IP throttled for over twenty minutes). Unit search latency is not the problem: 129
recorded searches took a median 3.53 s. Under replay, the evaluation's mode, search cost is near zero and N2 is
achievable. The requirement fails for live production, which does not exist yet. (`Phases.md` Phase 5 exit
criterion, `docs/searxng.md`.)

---

## 7. Evaluation metrics

Formulas are reproduced exactly from the research design. Baseline is **Baseline B** (fixed multi-agent workflow, no memory, no routing) unless stated.

**API Call Reduction**

```
ARR = ((Calls_baseline − Calls_proposed) / Calls_baseline) × 100
```

**Search Reduction**

```
SRR = ((Searches_baseline − Searches_proposed) / Searches_baseline) × 100
```

**Cost Reduction**

```
CR = ((Cost_baseline − Cost_proposed) / Cost_baseline) × 100
```

`Cost` is measured in **tokens** (primary). CR is also reported on list-price USD, labelled as
list-price-derived: actual spend is zero because every service runs on a free tier
(`Rules.md` §4, `docs/evaluation-plan.md` §4).

**Latency Reduction**

```
LR = ((T_baseline − T_proposed) / T_baseline) × 100
```

### 7.1 Quality assessment — weighted rubric

| Dimension | Weight | Scored by |
|---|---:|---|
| Product relevance | 20% | Human rater + LLM judge |
| Customer relevance | 15% | Human rater + LLM judge |
| Brand consistency | 15% | Automated brand-voice check — **NOT IMPLEMENTED** (no voice vector, no similarity; `docs/decision-log.md` 3.5). Scored by the human raters until it is |
| Evidence quality | 15% | Fraction of claims with live, resolvable sources |
| Trustworthiness | 10% | Hallucination audit, manual |
| Originality | 10% | Human rater |
| Feasibility | 10% | Human rater |
| Cost efficiency | 5% | Tokens per approved campaign (primary), with list-price USD per approved campaign alongside — labelled list-price-derived, not money paid |
| **Total** | **100%** | |

> **Methodological warning.** An LLM judge scoring output produced by the same model family is a weak instrument. For the dissertation, use at least two human raters on a sample of ≥ 30 campaigns and report inter-rater agreement (Cohen's κ). Report the LLM-judge score separately and label it as such. Do not present a single blended number as if it were an objective measurement.

---

## 8. Experimental design

| Arm | Configuration | Purpose |
|---|---|---|
| **Baseline A** | Single LLM call, no memory, no research, no agents | Floor — what a prompt alone achieves |
| **Baseline B** | Fixed 7-agent workflow, all agents always run, full research always, single model | Isolates the benefit of *adaptivity*, not of *agents* |
| **Proposed** | Adaptive memory + adaptive research depth + adaptive agent selection + model routing | The contribution |

**Protocol.** 30 campaign requests across ≥ 5 sectors. Each request run once per arm. For the memory benefit to be visible, requests must include **repeat and near-repeat requests for the same brand** — a cold-start-only test will show zero memory advantage and invalidate the central claim.

**Required reporting.** Mean and standard deviation per metric, paired t-test or Wilcoxon signed-rank with p-values, and the raw per-run log as an appendix. Report runs that failed or hit the cost cap; do not drop them.

---

## 9. Cost governance

| Parameter | Value |
|---|---:|
| `max_llm_calls` | 20 |
| `max_search_queries` | 20 |
| `max_revision_rounds` | 2 |
| `max_media_generations` | 4 |
| `max_estimated_cost_usd` | 2.50 |

**Zero-rupee constraint.** Every service runs on a free tier; nothing is paid. Tokens are the
primary cost unit. `max_estimated_cost_usd` is computed from list prices and stays as a safety
limit — it bounds what a run *would* cost, not what it costs.

Enforcement is **pre-flight, not post-hoc**: estimated cost is computed before each call and the call is refused if it would breach the cap. A breached cap produces a partial result with an explicit `budget_exhausted` marker, never a silent truncation.

---

## 10. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Model IDs and pricing drift** | High | Build breaks or cost estimates go stale | Pin Gemini 3.x model IDs from day one — better price/capability than 2.5, not because 2.5 has an announced shutdown (it does not, as of 14 Oct 2026). Re-verify IDs, prices and shutdown dates at `ai.google.dev` before every deploy. See `Architecture.md` §4. |
| R2 | Memory poisoning — a bad fact is stored and reused forever | Medium | High | Only approved, sourced facts are written; every memory row carries provenance and can be revoked |
| R3 | Memory benefit does not appear in evaluation | Medium | Fatal to thesis | Design the test set with deliberate repeat requests (§8) |
| R4 | SearXNG instance rate-limited or blocked | High | Medium | Self-host on the developer's laptop (`Architecture.md` §11); cache aggressively; on failure, degrade to a partial result. No paid SERP fallback: the project spends nothing (`Rules.md` C0) |
| R5 | Scope overrun — 7 agents is a lot for a student team | High | High | Phase 6 ships 5 agents (A1, A2, A3, A6, A7). A4 and A5 are deferred; regulated-sector safety is carried by the sector constraint registry, not A4. See `Phases.md` §6.1. |
| R6 | Cost cap makes D2 runs useless | Medium | Medium | Tune caps empirically in Phase 10 and report the tuned values |
| R7 | Examiner asks why the paper's RF engine is missing | Certain | Medium | §1.2 D1 — rehearse it |

---

## 11. Definition of done

The project is done when all of the following are true:

1. A new user can sign up, add a brand and a product, and receive an approved campaign without developer intervention.
2. The same brand's second campaign demonstrably costs less and makes fewer searches than its first, with logs proving it.
3. Every claim in a delivered campaign resolves to a source or is labelled as a deduction.
4. The experimental harness produces ARR, SRR, CR and LR across all three arms with raw logs.
5. The public site renders the 3D hero at target frame rate and degrades cleanly without WebGL.
6. `Rules.md` has not been violated anywhere in the codebase.

---

## 12. Traceability — gap to feature

| Paper gap | BrandverseAI feature | Requirement ID |
|---|---|---|
| G1 Workflow fragmentation | Single pipeline, 15 steps, one workspace | M1, M8 |
| G2 Predictive–generative disconnect | Memory scoring feeding generation | M3, M10 |
| G3 Privacy paradox | Zero-retention research cache, consent-scoped memory, no consumer PII | §4.3, N7 |
| G4 Algorithm aversion | Human approval gate, revision rounds | M9 |
| G5 Opaque attribution | Performance feedback step; Shapley deferred | C7 |
| G6 Ethical norm violations | Trust & Risk Agent A7, restricted-claim blocking | M2, M11 |
| G7 Production reproducibility | This document set; seeded demo data; harness | M12 |
