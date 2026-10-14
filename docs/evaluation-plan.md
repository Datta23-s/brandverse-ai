# BrandverseAI — Evaluation Framework (draft for written agreement)

| Field | Value |
|---|---|
| Phase | 0 — D1–D4 agreed 2026-10-14; D5 open (see §8) |
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
| D3 | **One approver for all runs**, across all arms, working from a written approval rubric. The rubric is committed to the repo before the first evaluation run and is not changed during evaluation. | 2026-10-14 — rubric not yet written |
| D4 | **10 brands across ≥ 5 sectors, including ≥ 1 regulated sector, with real, publicly findable product specs under pseudonymised brand and product names** (option (a) below). RQ1 is in effect. | 2026-10-14 |
| D5 | **Organisation layout for evaluation brands.** If all 10 brands share one organisation, org-wide memory (`brand_id is null`) from one brand can reach another brand in the same sector, so a brand's campaign 1 is no longer cold-start (B01/B02 share a sector). Options: one organisation per brand (every campaign 1 is a true cold start), or one shared organisation (the agency persona; cross-brand sector reuse becomes part of what is measured, and must be reported as such). | **Open** |

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
and requests for the tuning set are not yet researched. Parent-company overlap was not checked.

## 9. Required items

Each item is a hard requirement, not guidance. An item that applies and is not met blocks the
evaluation results from being reported.

| # | Applies when | Requirement | Verified by |
|---|---|---|---|
| RQ1 | In effect (D4 = option (a)) | **Disclosure in the dissertation methodology section.** It must state that the evaluation used real product specifications under pseudonymised brand names, so that the claim gate was exercised against checkable facts without generating brand-impersonating output (`Rules.md` T8). | Methodology section reviewed against this wording before submission |
| RQ2 | Before any evaluation run | **Pseudonym collision check.** Each brand and product pseudonym in `evaluation/test_set.yaml` is searched for and confirmed not to be an existing brand or product. A pseudonym that collides with a real brand defeats the purpose of D4 and T8. Not yet done. | Search log committed alongside the test set |
| RQ3 | Before any evaluation run | **Registry populated for every regulated evaluation sector** (currently `health-supplements`, B07). Rows entered by a human from the cited regulation or guidance, with `source_url`. Until then the registry fails closed and every B07 run is blocked; such runs would be reported as blocked, not dropped (`Rules.md` A2). No rows have been entered or drafted. | `sector_constraints` rows for each regulated sector, each with a source |
