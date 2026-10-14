# BrandverseAI — Evaluation Framework (draft for written agreement)

| Field | Value |
|---|---|
| Phase | 0 — D1–D5 agreed 2026-10-14 (see §8) |
| Track | Thesis-critical (5 agents: A1, A2, A3, A6, A7; regulated-sector safety via the sector constraint registry) |
| Source of truth | `PRD.md` §7–§8, `Memory.md` §7–§8, `Rules.md` §9 |
| Date | 14 October 2026 |

No results appear in this document. It defines what will be measured and how.

## 1. Arms

| Arm | Configuration on the thesis-critical track |
|---|---|
| **Baseline A** | Single LLM call. No memory, no research, no agents. |
| **Baseline B** | All built agents (A1, A2, A3, A6, A7) always on, full research (D2) always, single model tier. No memory, no routing. The sector constraint registry applies to every arm — it is a safety gate, not an adaptive feature. |
| **Proposed** | Adaptive memory + adaptive research depth (D0/D1/D2) + adaptive agent selection (3–5 of 5) + model routing. |

The Baseline B definition above applies to the evaluation harness only. `Architecture.md` §7 continues
to describe the full-track 7-agent design. Because A2 and A5 are not built on this track, the
dissertation must state that Baseline B is "fixed, all-built-agents" rather than "fixed 7-agent".

All arms share the same cost caps (`PRD.md` §9: 20 LLM calls, 20 searches, 2 revisions,
4 media generations, $2.50). A capped run is recorded, not retried.

## 2. Test set

- 10 brands × 3 campaigns = **30 requests**, across ≥ 5 sectors, including ≥ 1 regulated sector.
  The set is in `evaluation/test_set.yaml` (9 sectors; health supplements is the regulated one).
- Campaign 1 per brand is cold-start. Campaigns 2 and 3 are related to campaign 1 (same brand;
  same or adjacent product). This is the progression in `Memory.md` §7.
- Requests are run **in order per brand** in the Proposed arm, so memory from campaign 1 is
  available to campaigns 2 and 3. Baselines A and B have no memory, so order does not affect them.
- Each request is run once per arm. Every run is logged to `runs` and `llm_calls`.
- Write-back in the Proposed arm requires a human approval (`Rules.md` T6). One approver
  handles every run in every arm, working from a written rubric (decision D3, §8).

## 3. Seed exclusion

Seed memory (`Memory.md` §7.1) is excluded from every evaluation run by a filter in the harness
code, and a test asserts that no seeded item appears in any evaluation run's retrieved set.

Calibration decisions (`docs/approval-rubric.md` §6) are excluded the same way: they are kept in
`approvals` with `is_calibration = true`, and the harness reads decisions only through
`backend/app/evaluation/approvals.py`, which filters on that flag (never on the practice
organisation's id). `backend/tests/db/test_rls.py::test_calibration_row_never_reaches_evaluation_query`
asserts that a calibration row — including one flagged inside an evaluation organisation — never
reaches the evaluation query. The practice organisation is marked `organisations.is_practice`; the
database rejects an approval there without `is_calibration`, and the harness query excludes
practice organisations as well (`test_unflagged_practice_approval_never_reaches_evaluation_query`).

## 4. Metrics

Computed per run from `runs` / `llm_calls`, then aggregated. Baseline is Baseline B unless stated.

| Metric | Formula |
|---|---|
| ARR | `(Calls_baseline − Calls_proposed) / Calls_baseline × 100` |
| SRR | `(Searches_baseline − Searches_proposed) / Searches_baseline × 100` |
| CR  | `(Cost_baseline − Cost_proposed) / Cost_baseline × 100` |
| LR  | `(T_baseline − T_proposed) / T_baseline × 100` |

Reported additionally, broken down by campaign position (1st / 2nd / 3rd per brand), since
that is where the memory effect is expected: depth distribution (D0/D1/D2), memory hit rate,
coverage, reuse depth (`Memory.md` §8).

## 5. Statistics

- Mean and SD per metric per arm.
- Paired comparison per request (Proposed vs Baseline B, Proposed vs Baseline A): paired t-test
  if differences pass a normality check, otherwise Wilcoxon signed-rank. Report p-values.
- Raw per-run logs committed as an appendix. Failed and capped runs included.

## 6. Quality

- Rubric and weights per `PRD.md` §7.1.
- Two human raters who did not build the agents rate all 30 requests' outputs in every arm,
  blind to arm. Cohen's κ reported per rubric dimension (decision D2, §8).
- LLM-judge score reported separately and labelled as such; never blended into the human score.

## 7. Tuning

`R` and `M` weights are tuned in Phase 10. To avoid tuning on the test set, tuning uses the
50-query relevance set (`Memory.md` §8) and a separate tuning set of 10 requests on brands that do
not appear in the 30-request evaluation set (decision D1, §8). Defaults and tuned values are both
reported (`Rules.md` A5).

## 8. Decisions

| # | Decision | Agreed |
|---|---|---|
| D1 | **Tuning set is disjoint from the evaluation set.** 10 tuning requests on 10 brands that do not appear among the 30 evaluation requests. No tuning-set brand, product or request may be used in evaluation, and the harness asserts the two sets share no brand. | 2026-10-14 |
| D2 | **Two raters who did not build the agents**, rating blind to arm. Cohen's κ reported per rubric dimension. LLM-judge scores reported separately (§6). | 2026-10-14 |
| D3 | **One approver for all runs**, across all arms, working from the written rubric **`docs/approval-rubric.md`** (v1.2). Its §6 requires a **calibration pass before run 1**: 10 practice campaigns on brands in **neither** the evaluation set nor the tuning set, scored by the approver and independently by a second team member, the rubric amended until they agree on ≥ 8/10; practice campaigns and their approvals are kept, flagged `is_calibration`, and excluded from evaluation by the harness (§3). Its §7 **forbids silent mid-evaluation amendment**: any change after run 1 is recorded with its reason and run number, and earlier runs are re-scored or the two periods reported separately. | 2026-10-14 — rubric v1.0 committed; v1.1 (§2 presentation order: constrained randomisation) and v1.2 (§5–§6 calibration rows kept in `approvals`, flagged and filtered) committed before calibration; approver not yet named; calibration not yet done |
| D4 | **10 brands across ≥ 5 sectors, including ≥ 1 regulated sector, with real, publicly findable product specs under pseudonymised brand and product names** (option (a) below). RQ1 is in effect. | 2026-10-14 |
| D5 | **One organisation per evaluation brand: ten organisations.** Every brand's campaign 1 is a true cold start. *Rationale:* a shared organisation creates an order effect — later brands inherit earlier brands' org-wide memory (`brand_id is null`), which systematically shrinks the D2→D0 delta for reasons unrelated to the contribution. Cross-brand transfer is measured separately, in the optional secondary experiment (§10), never in the primary. | 2026-10-14 |

### D4 and `Rules.md` T8

T8: *"The system never generates content impersonating a real person or a real brand it does not
own."* Generating campaigns for real brands' products conflicts with that rule, and `Rules.md` §0
ranks Rules above every other document and requires the conflict to be resolved explicitly, not
silently. The sector requirements in D4 do not conflict; only "real brands" does.

Resolved 2026-10-14 as option (a). Options considered:

| Option | Effect |
|---|---|
| (a) Real products, fictional brand names | Specs remain real and verifiable (so verified claims have live sources); brand names, logos and voice are invented. T8 unchanged. Evidence URLs would point at the real manufacturer, which partly reveals the brand. |
| (b) Amend T8 with a scoped evaluation exception | E.g. real brands permitted for internal, unpublished evaluation outputs only, marked AI-assisted (T7), never deployed or shown publicly. Requires editing `Rules.md`. |
| (c) Fictional brands and products | Fully T8-compliant, but verified claims have no independent public source, which weakens the evidence-quality dimension. |

### The 30-request set — `evaluation/test_set.yaml`

| Position | Product | Expected (hypothesis, from `Memory.md` §7 — not a result) |
|---|---|---|
| 1 | Product A, cold start | D2 |
| 2 | Product B: same brand, related product | D1 |
| 3 | Product A again, new objective | D0 or shallow D1 |

- Each product's claims were read from the manufacturer's own product record on 2026-10-14; every
  evidence URL returned HTTP 200 on that date. 83 claims are proposed `verified`, 33 proposed
  `restricted`, 5 recorded as conflicts. Types are **proposals** for the human who enters
  `product_claims`; nothing is verified by the file itself.
- Restricted claims (health, medical, superlative, "India's first") are kept on purpose: they are
  real manufacturer copy the claim gate must block, so they test T2/T3/T5 against real input.
- Conflicts (e.g. one page giving two Bluetooth versions) are test material for the
  conflicting-evidence route to the strong tier (`Architecture.md` §4.2).
- Some figures in the conflicts came from search-result summaries, not fetched pages; they are
  marked UNCONFIRMED in the file and must not be entered as claims.
- `budget_tier` is identical for all 30 requests, so budget is not a confound.

### Tuning set — `evaluation/tuning_brands.yaml`

10 brands chosen, one per sector used in evaluation plus a second consumer-electronics brand,
including one regulated (health supplements). **No real brand is shared with the evaluation set**,
checked on 2026-10-14 by comparing normalised real brand names, not pseudonyms. Products, claims
and requests for the tuning set are not yet researched. Parent-company overlap is not yet checked
(RQ2b, blocking).

## 9. Required items

Each item is a hard requirement, not guidance. An item that applies and is not met blocks the
evaluation results from being reported.

| # | Applies when | Requirement | Verified by | Status |
|---|---|---|---|---|
| RQ1 | In effect (D4 = option (a)) | **Disclosure in the dissertation methodology section.** It must state that the evaluation used real product specifications under pseudonymised brand names, so that the claim gate was exercised against checkable facts without generating brand-impersonating output (`Rules.md` T8). | Methodology section reviewed against this wording before submission | ☐ Not yet verified |
| RQ2a | **Blocking** — before any evaluation run | **Pseudonym collision check.** Every invented brand and product name in `evaluation/test_set.yaml` is searched and confirmed not to be an existing real brand or product **in any sector**, not only the brand's own. A pseudonym that collides with a real brand defeats the purpose of D4 and T8. | Search log (query, date, result per name) committed alongside the test set | ☐ Not done |
| RQ2b | **Blocking** — before any evaluation run | **Parent-company check across both sets.** Confirm that no evaluation brand (`evaluation/test_set.yaml`) and tuning brand (`evaluation/tuning_brands.yaml`) share a parent company. A shared parent would leak positioning and spec language between sets that D1 requires to be disjoint. Brand-name disjointness is already confirmed (§8); this check goes one level up. Ownership must be taken from a dated, citable source (e.g. company filings or announcements), not from memory. | Ownership table with a source per brand, committed alongside both sets | ☐ Not done |
| RQ3 | Before any evaluation run | **Registry populated for every regulated evaluation sector** (currently `health-supplements`, B07). Rows entered by a human from the cited regulation or guidance, with `source_url`. Until then the registry fails closed and every B07 run is blocked; such runs would be reported as blocked, not dropped (`Rules.md` A2). No rows have been entered or drafted. | `sector_constraints` rows for each regulated sector, each with a source | ☐ Not done |

## 10. Secondary experiment (optional): cross-brand transfer

**Optional. Runs only after the primary evaluation (§1–§7) is complete and its results are
reported.** It is a separate finding, reported separately, and is **never merged or pooled into
the primary results**. It tests the agency persona in `PRD.md` §3 (Arjun: reuse intelligence
across brands instead of re-researching).

| Aspect | Design |
|---|---|
| Question | Does org-wide memory learned from one brand reduce calls, searches, cost and latency for *other* brands in the same organisation? |
| Setup | One **new** organisation holding all 10 evaluation brands. It starts with empty memory — nothing carries over from the primary's ten organisations. |
| Requests | The same 30 requests from `evaluation/test_set.yaml`. |
| Arm | Proposed only. Baselines A and B have no memory, so organisation layout cannot change them; the comparison is with the primary's Proposed runs. |
| Comparison | Paired per request: shared-organisation Proposed vs primary (per-brand organisation) Proposed. Same metrics and tests as §4–§5. The headline is each brand's **campaign 1**, which is cold-start in the primary and may not be here. |
| What can transfer | Only org-wide memory (`brand_id is null`). Brand-scoped items stay with their brand by `match_memory`'s filter (`Memory.md` §4.2). Each cross-brand hit is attributed from its provenance (source run, hence source brand). |
| **Required output: transfer by sector relation** | Cross-brand memory hits — and the per-request deltas they explain — are reported **broken down by same-sector vs cross-sector** (source brand's sector equal to, or different from, the receiving brand's sector), as well as by source brand. **A single blended transfer figure is not reported on its own**: B01 and B02 share a sector (consumer electronics), so blending would hide whether the effect comes from same-sector reuse or genuine cross-sector transfer. Note the sample: in B01 → B10 order, same-sector transfer is possible only into B02 (from B01); every other brand's sector is unique in the set, so same-sector results rest on one brand pair and are reported descriptively, not with significance tests. |
| Order | **Fixed 2026-10-14: test-set order B01 → B10, each brand's three campaigns consecutive** (R01, R02, R03, R04, … R30). The result depends on order — that is the effect being measured — so it does not change once the experiment starts. |
| Approval and rating | Same approver and rubric as the primary (D3), so write-back conditions match. |
| Prerequisites | Everything the primary requires, including RQ2a, RQ2b and RQ3. |

### Approver record (`docs/approval-rubric.md` §2, §6, §8)

| Field | Value |
|---|---|
| Approver | not yet named |
| Calibration date | not yet done |
| Calibration agreement | — / 10 (≥ 8 required) |
| Practice-campaign brands | not yet chosen; must be in neither `evaluation/test_set.yaml` nor `evaluation/tuning_brands.yaml` |
| Practice organisation | not yet created; `is_practice = true`, holds only the practice campaigns. The approver and the second scorer both hold `approver` there |
| Second scorer | not yet named; `approver` role in the practice organisation only |
| Rubric version applied | 1.2 |

### Points between the rubric and this plan (resolved 2026-10-14)

- **Presentation order — resolved by decoupling generation from approval.**
  *Clash:* rubric §2 presents runs in randomised order, but in the Proposed arm a brand's
  campaign N+1 cannot be generated until campaign N is decided (memory write-back requires
  approval, `Rules.md` T6), so approvals in run order would be grouped by brand and sequenced.
  *Resolution:*
  1. **Generation** runs 1 → 2 → 3 within a brand, as the memory dependency requires. Campaign
     N+1 of a brand is generated only after campaign N has a decision.
  2. **Approvals** are queued separately from generation. Completed campaigns from every arm and
     brand enter one approval queue, presented in **shuffled batches** (at most 15 per batch —
     the rubric §2 session cap), arm labels stripped.
  3. **Adjacency constraint:** a brand's campaign N and N+1 are **never adjacent in the approval
     queue**, in any arm and across batch boundaries (the last item of one batch and the first of
     the next count as adjacent). A shuffle that violates this is redrawn.
  4. The shuffle seed and the resulting queue order are logged per batch, so the presentation
     order is reproducible and reportable.
  *Fallback measurement:* sequencing may still let the approver infer the arm. Every decision
  records `arm_inferred` and `arm_inference_note` (rubric §5), and the arm-inference rate is
  reported per arm. This is the measurement if the decoupling does not fully blind the approver.
- **Where the approval record lives — resolved.** Decisions go in the `approvals` table
  (migration `0009_approvals.sql`, `docs/erd.md` E13): one row per decision — approve, revise,
  reject or undecided — with `failed_gate`, `reason`, `seconds_spent`, `arm_inferred`,
  `arm_inference_note` and `decided_at`. It is separate from `campaigns.quality_scores`, which
  holds only the two blind raters' scores (§6). It was added in Phase 1, not retrofitted before
  Phase 6.

