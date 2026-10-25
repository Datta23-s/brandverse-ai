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
- [x] Sign up → create brand → create product works end to end — **ticked 2026-10-17 on
  observed evidence** (un-ticked earlier the same day, at 05:20 UTC, when no brand or product
  had been seen). Observed on the live project, all as the owner in an organisation bootstrapped
  by `app.create_organisation` (0012), not as the fixed E2E users: sign-up through the form,
  email confirmed through the real confirmation email, sign-in, organisation created
  (`POST /api/v1/organisations` → 201 at 05:09:05 UTC); then, in the live API's access log,
  `POST /api/v1/brands` → 201 at 05:21:58 UTC and `POST /api/v1/products` → 201 at 05:22:15 UTC,
  and later list requests for that brand's products → 200. Read back from the database (read-only
  transaction, 2026-10-17, about 12:50 UTC): one brand (`created_at` 05:21:58.416) and one product under
  it (`created_at` 05:22:15.780), both in the owner's organisation, whose only member is the
  owner; they are the project's only brand and product rows. Each row's `created_at` is about
  0.22 s after its request's log line, the same offset as the organisation row (database clock vs
  laptop clock), so every 201 has its row: no create returned success without persisting.
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

**Exit criteria** (ticked 2026-10-17 on mocked calls; zero live model calls; `Architecture.md` §4.4)
- [x] A deliberate over-budget run aborts cleanly with partial output —
  `backend/tests/db/test_budgeted_run.py`: four steps, one call each, against the real ledger and
  database. With a 2-call cap, and separately a $0.004 cost cap, the third call is refused
  pre-flight (the mocked model is never asked), the run returns the first two steps' outputs and
  names the two not run, flagged `budget_exhausted` (`Rules.md` C4). The `runs` row reads
  `partial`, `budget_exhausted = true`, 2 calls; the refusal is in `llm_calls` with its reason.
  The organisation's monthly cap ends a run the same way (added 2026-10-17: a $0.01 cap with
  $0.0075 already spent this month refuses the second call; `partial`, flagged, 1 call).
  Mechanism: `app/core/budgeted_run.py`, which the Phase 6 pipeline must use.
- [x] Cost per call appears in the database within one second — 20 mocked calls, reply to
  cost readable from a separate connection: min 9.5 ms, median 11.3 ms, max 19.4 ms (local
  Postgres; asserted < 1 s per call in the merge-blocking suite). One real call: 24 ms. On the
  live pooler, a transaction of `finish()`'s shape took 35–48 ms (read-only proxy, 20 runs).
- [x] Switching a tier's model is a one-line config change — `scripts/demo_tier_switch.py`
  (mocked provider): one edited line in a copy of `config/models.yaml`; the provider received
  the new model string and `llm_calls` recorded it; the other tiers were unchanged. Also
  `test_switching_a_tiers_model_is_a_one_line_config_change`, merge-blocking.

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
- [x] A product with no verified claims cannot start a run — **ticked 2026-10-17 on observed
  evidence.** On the live project (migrations 0015–0020 applied 14:01 UTC), the live E2E spec
  "a product with no verified claim cannot start a run…" created a product with no claims, started
  a run through the form and was refused, nothing started (`409`, reason `no_verified_claims`);
  read back from the database: `audit_log` rows `run.blocked`, actor `service`/`run_admission`,
  reason `no_verified_claims`, at 15:16:46 and 15:23:05 UTC (two runs), and `runs` still holds 0
  rows. Runs for the fixture product, which has an active verified claim, were admitted and
  streamed (run-view specs, 13/13 live). Also in CI and locally against the real migrations:
  `tests/db/test_api.py::test_a_freshly_bootstrapped_orgs_product_runs_only_once_it_has_a_verified_claim`
  (an organisation 0012 created; a restricted claim alone does not admit). On the fake-run path:
  Phase 6's `POST /api/v1/runs` must call the same `app/core/admission.py`.
- [ ] An attempt to generate a restricted claim is blocked and logged — **PROVISIONAL, not settled
  (2026-10-18).** Phase 3 cannot exit on this until a generator exists and real output passes the
  gates. Shown on hand-written sample copy with a mocked model only:
  `tests/test_claim_classifier.py::test_criterion_2_a_restricted_claim_in_output_is_blocked_whatever_the_model_cites`
  (the model cites a verified claim for the restricted statement; the risk gate scans the copy and
  blocks it anyway) and `…_the_model_ignores_is_still_blocked`; logged:
  `tests/db/test_claim_classifier_db.py::test_a_restricted_claim_in_output_is_blocked_and_logged`
  (`output.blocked` in `audit_log`). A brief asking for a restricted claim is refused at admission
  (`tests/db/test_api.py`). Restricted claims never reach a model's prompt. Paraphrase is not caught
  (threat V2).
- [ ] Every claim in a sample output resolves to a source — **PROVISIONAL, not settled
  (2026-10-18).** Same condition. Shown on two sample outputs built on real product material from
  `evaluation/test_set.yaml` (Tidewell Loop N2; Verdano Whey, regulated, on the strong tier):
  `tests/test_claim_classifier.py::test_criterion_3_every_claim_in_a_sample_output_resolves_to_a_source`
  — every extracted statement resolved to a verified claim and its evidence URL, none left over; a
  statement no claim supports blocks the copy. The model's extraction is scripted, so how well a
  real model extracts is not measured.
- [ ] Output in a regulated sector missing a required disclaimer is blocked — **PROVISIONAL, not
  settled (2026-10-17; box left open 2026-10-18).** Phase 3 cannot exit on this until a generator
  exists and real output passes the gates. Shown on test evidence only: `tests/db/test_gates_db.py::test_criterion_4_regulated_output_missing_a_required_disclaimer_is_blocked`:
  the gates (`app/core/gates.py`) loaded the stored health-supplements rules through
  `load_product_truth` — the same four rows as on the live project (0021, applied 16:04 UTC) — and
  copy mentioning protein without the triggered disclaimer was blocked (`missing_disclaimer`); a
  reworded disclaimer is a distinct `altered_disclaimer` block (`tests/test_gates.py`); compliant copy
  passes. No generator exists yet, so no live output was checked; the rules are illustrative.
- [ ] Output containing a registry-prohibited phrasing is blocked — **PROVISIONAL, not settled
  (2026-10-17; box left open 2026-10-18).** Phase 3 cannot exit on this until a generator exists
  and real output passes the gates. Shown on test evidence only: `tests/db/test_gates_db.py::test_criterion_5_output_with_a_registry_prohibited_phrasing_is_blocked`:
  copy with "miracle cure" and "Guaranteed results" was blocked by both stored prohibited rules (a
  phrase and a regex), and the block was appended to `audit_log` (`output.blocked`,
  `test_a_block_is_written_to_audit_log`). As for criterion 4: the stored rules, no live output.
- [x] A regulated sector with no active registry entries blocks the run (fails closed) — **ticked
  2026-10-17.** Evidence: `tests/db/test_api.py::test_admission_blocks_each_failing_check_and_logs_it`
  (real migrations, real API, CI on `b694beb` and locally): a product with a verified claim under a
  brand in a regulated sector with no registry rows is refused with `registry_coverage` and a
  message naming the fail-closed rule, and audit-logged; a brand with no sector is refused the same
  way; unit cases in `tests/test_admission.py` and `tests/test_sector_registry_fail_closed.py`
  (retired-only rules, other sectors' rules). When ticked, `health-supplements` had no rules; since
  0021 it has its illustrative rule set, so the test uses a test-only regulated sector with none.
  No live run in a rule-less regulated sector was attempted; this tick rests on the test evidence,
  stated as such.

> **Regulated-sector rules — resolved for the research with an illustrative rule set (decided by
> the project owner 2026-10-17; migration 0021).** Recorded earlier the same day as a human
> prerequisite: with no registry rows, the registry fails closed and every run for the evaluation's
> regulated brand (B07, R19–R21) and the tuning set's (T07) would be blocked. Instead of
> researching FSSAI or ASCI rules, `health-supplements` ships four **synthetic** rules (two
> prohibited phrasings, two required disclaimers), each with the source "Synthetic rule, not
> derived from any regulation. Illustrative only.", and the sector is marked
> `rules_provenance = 'illustrative'`, which every report naming it prints. The research claim is
> that the mechanism enforces whatever rules it is given, not that the shipped rules are legally
> correct (`Architecture.md` §8.1). **Substituting a real, cited rule set is a deployment step,
> outside the research contribution** — not a Phase 10 prerequisite.

**Phase 3 progress.** Chunk A (truth store, run admission, sector keys; 2026-10-17):
`Architecture.md` §8.2. Rule provenance and the illustrative rule set (0021): §8.1. Chunk B (output
gates, superlative list v1, registry authoring path, staged write-back; 2026-10-17): §8.3. The
brand-voice gate's registry half is built; its similarity score is Phase 6. Chunk C (the claim
classifier, `claim_extract@v1`; 2026-10-18): §8.3, mocked model only — zero real calls. **Phase 3
is built but cannot exit**: criteria 2–5 stay provisional until a generator (Phase 6) produces real
output that passes the gates.

> **Specified, not implemented: detectors for the eight predatory anti-patterns (`Rules.md` T5;
> decided 2026-10-18).** `Architecture.md` §8 specifies them for the risk gate. None is built, so
> none is in the gates' outcome table — listing them as blocking would claim enforcement that does
> not happen — and **T5 is unmet**. Reason: several cannot be detected from copy at all —
> undisclosed paid endorsement needs the commercial relationship, price anchoring on fictional
> prices needs real price history, vulnerability targeting depends on who is targeted, dark-pattern
> consent is a property of a UI flow, not of text — and keyword lists for the rest would be shallow
> detectors with an unmeasured miss rate, another claim the code could not back. Until a detector
> exists the approver checks them (`docs/approval-rubric.md` S2.1; threat V3). Where detectors are
> built (the natural home is A7's risk assessment, Phase 6) is not yet scheduled; whichever phase
> builds one adds its finding kind to the outcome table mapped to `block`, in the same change. Paraphrase of a restricted claim is not caught by deterministic matching —
threat V2 in `docs/evaluation-plan.md`.

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
- [x] Cross-tenant memory leak test green (CI; and on the live project 2026-10-18, rolled back)
- [x] Near-duplicate detection prevents a second insert at cosine > 0.95 (CI, `tests/db/test_writeback_commit.py`; no migration involved). Not checked on the live project. It will be confirmed there incidentally, from the `memory.writeback` audit rows, when real memory rows first land. It cannot be re-run there as a committed test: `memory_items` rows on live can never be deleted (0010, 0011), so every committed test row would be permanent memory. A check in a transaction that is always rolled back, as for the cross-tenant and revocation criteria, remains possible for everything except two concurrent write-backs (which must commit one side), and has not been run.
- [x] Revocation removes an item from retrieval immediately (CI; and on the live project 2026-10-18, rolled back)

**Phase 4 progress.** Decisions recorded 2026-10-18 (`Memory.md` §5, §8; `docs/evaluation-plan.md`
§7): embeddings computed locally by a pinned encoder; relevance labels by Kundan, blind. The
labelling tool is built. Chunk A (2026-10-18, migrations 0022–0023 not yet on the live project):
`app.match_memory`, the `M`-score with breakdown (`config/scoring.yaml`, defaults), freshness with
the verified-claim exemption, the reuse bands, memory coverage, and the two memory trust fixes.
Tested against the real migrations: the cross-tenant leak test (including a member of both
organisations) and immediate removal on revocation (`tests/db/test_match_memory.py`) — the two
criteria above are ticked once the migrations are live and checked there. The first push of 0022
was refused on 2026-10-18 (nothing applied; `Architecture.md` §11, the 0022 lesson): `hnsw.ef_search`
moved out of the function to the caller (`app/memory/retrieval.py`), where it is configured,
logged with every retrieval and can be swept (`Memory.md` §4.3). 0022 and 0023 pushed 2026-10-18
12:29 UTC. Both criteria were then checked on the live project in a transaction that was always
rolled back, with the E2E organisations: an identical item in E2E Org B never reached E2E Org A's
owner, and asking for Org B returned nothing; revoking Org A's item removed it from the next
retrieval, recorded the revoker and `now()` despite a back-dated value sent, and un-revoking was
refused. `memory_items` held 0 rows before and after.

Chunk B (2026-10-18): the local encoder (`app/memory/embeddings.py`, `Memory.md` §5 as built) —
pinned revision, weights hash checked on load, `Normalize` and dimension checked, queries and
documents embedded by separate methods; the configured dimension asserted equal to the vector
columns'. Embedding pipeline deliverable: the encoder is built; chunking and storing come with
write-back (chunk C).

Chunk C (2026-10-19): write-back (`app/memory/writeback.py`, `Memory.md` §6 as built), in two
halves. Screening, when a candidate is staged: it must rest on a verified, active claim the run's
gate report resolved with no finding against it (so a V4 flag keeps it out); `deduced` is refused
until Phase 5 scores sources; prices, discounts, dates, promo codes, email addresses and
phone-number-shaped digit runs are refused in the forms the screens name, and anything else
campaign-specific or personal is not detected; content over 400 tokens is refused, not split.
Eligible candidates are embedded in one batch and staged with the encoder's id and revision.
Commit, inside the terminal transaction (`end_run`): the run's campaign must be approved by a human,
or the whole commit is refused and the run ends `failed`; a claim retired since staging drops its
item; then near-duplicates (cosine > 0.95, exact scan, same organisation, brand, kind and encoder
revision, seed items excluded) are reinforced, not inserted, a revoked near-duplicate is never
written again, and one write-back counts a fact once. Write-backs of one organisation are
serialised by an advisory lock, tested with two concurrent commits. Every commit writes one
`audit_log` row. The near-duplicate criterion above is ticked from CI; no migration was needed.
A test found that reinforcing an item with a null confidence set it to the 0.99 cap (`least()`
ignores nulls); fixed before commit.

Chunk D (2026-10-19/20): §6.3 and the step-15 node (`Memory.md` §6, §6.3, §6.4 as built). Owner's
rulings 2026-10-19: approval gates what adds trust, never what reduces it; corrections commit at
every ending that is not an infrastructure failure, `failed` included; a revocation by decay is
the system's act, recorded with a constrained reason, and how items died is an evaluation count
(`docs/evaluation-plan.md` §4). Built: decrements and revocation by rule below 0.30, reuse bumps,
the step-15 node `write_memory` (stages; `end_run` commits), the attempt rule split by trust in
`budgeted_run.py`, `app/evaluation/memory_revocations.py`. Migration 0024 (decay revocation
record): live dry run 2026-10-20, approved by the owner, applied 2026-10-20 10:42 UTC (posture
54/54 after; `Architecture.md` §11). What feeds these — the validation step's flags against memory items, the reviewer's
edit-outs, the campaign's list of reused items — is wired with the graph in Phase 6. Migration 0025
(2026-10-20, owner's ruling): `memory_items.confidence` is `NOT NULL`, no default, so no item can be
exempt from decay by having no confidence; its own dry run and approval, separate from 0024.
Applied 2026-10-25 13:11 UTC after CI green on 4143eec (`migration list` identical; the column
NOT NULL, no default, 0 rows). Migrations 0026 and 0027 (owner's ruling 2026-10-25): confidence is
a probability and a live item is never below the revocation threshold — revocation tests the
value, not the transition (`Memory.md` §6.3); 0027 then drops the negative from 0024's decay
pattern. Live dry run passed; CI green on d75ab2f; both applied together 2026-10-25 13:27 UTC
(`migration list` identical through 0027). The posture suite `test_table_posture.py`, run unchanged
against the live project in a read-only session after 0027 (its first run since 0023): 111 of 111.

**A finding Phase 4's preparation surfaced: V4, citation does not imply entailment**
(`docs/evaluation-plan.md` §4). It was not designed as a test. In the cheap-tier calls confirming
the structured-output fix (2026-10-18, before chunk A), the claim classifier cited the verified
claim "Up to 60 hours playback (at 60% volume)" for the planted statement "Lasts all week." — and
the claim gate passed it, correctly by its own rules, because it resolves citations and never
checks entailment. Recorded verbatim there with the numbers (60 hours is 2.5 days; a week is 7),
followed by a mechanical partial mitigation (flag a statement that drops a cited claim's quantity)
and the rejected alternative (a model-based entailment check).

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
| Budget behaviour reused, not rebuilt | The graph's LLM steps run under `app/core/budgeted_run.py` (decided 2026-10-17, `Architecture.md` §4.4): a budget refusal ends the run `partial` with `budget_exhausted`, and the graph writes only the run statuses defined there. No second executor |

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
| **Close the C4/C9 daily-quota gap** (recorded 2026-10-17) | A call that would breach its model's daily quota is downgraded to a lower tier with the reason in `llm_calls.route_reason`; a T4-required strong-tier call is deferred until the quota resets; the run degrades instead of failing (`Rules.md` C4, C9). **Until this lands, a run that exhausts a daily quota fails** (`QuotaExhausted` → `runs.status = 'failed'`) — see `Architecture.md` §4.4, "Known divergence from C4 and C9" |
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
| **Opt-in experiment membership** | Built as designed in `Architecture.md` §9, "Experiment membership" (approved 2026-10-17): `experiments` immutable once referenced; `experiment_id`, `arm`, `request_ref`, `attempt` set together by the harness role or forced null; every evaluation read selects by experiment. Attempt rule pre-registered 2026-10-17 in `docs/evaluation-plan.md` §2 — the primary result is the first attempt that terminated without a listed infrastructure failure, recorded mechanically in `runs.infrastructure_failure` |
| **Hash chain on `approvals` and `audit_log`** | Built and verified **before calibration begins** (`Architecture.md` §9, decision 2): tamper evidence for the decisions the results rest on — detection, not prevention |

**Regulated sector:** runs against the illustrative synthetic rule set (Phase 3, 0021). Every
table or figure that names a sector prints its rules' provenance (`docs/evaluation-plan.md` §4).

**Exit criteria**
- [ ] Every reported number traces to a committed raw log
- [ ] Failed and capped runs included in the report, and gate-blocked runs counted per arm as a
  result in their own right (`runs.status = 'blocked'`, `backend/app/evaluation/run_outcomes.py`)
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
