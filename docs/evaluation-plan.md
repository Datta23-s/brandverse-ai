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

### Run selection and the attempt rule — pre-registered 2026-10-17, before any data exists

Fixed on **2026-10-17**, when the live project held **0 runs** and no evaluation organisation
existed (`Architecture.md` §11, "Live project — permanent footprint", read 15:24 UTC that day).
Any later change is an amendment, recorded with its date and reason, and the rule as written here
is still reported alongside it.

**Membership is opt-in.** A run belongs to the evaluation only if it carries an experiment marker:
`experiment_id`, `arm`, `request_ref` (R01–R30) and `attempt`, all set together by the harness's
own database role or not at all (design: `Architecture.md` §9, "Experiment membership"). Every
evaluation read selects by `experiment_id`; an unmarked run — a manual run, a demo, a debugging
run, a run in an organisation created by accident — is excluded by default. The exclusion flags
(`is_practice`, `is_test`) stay as a second defence. Forgetting a marker removes data; it can never
add data.

**The attempt rule (verbatim, as fixed by the project owner on 2026-10-17):**

> All attempts are reported. For each request-arm pair, the primary result is the FIRST attempt
> that terminated without infrastructure failure. Infrastructure failure is defined in advance and
> narrowly: rate-limit rejection, network error, process crash, daily-quota exhaustion. Nothing
> else qualifies.
>
> A run that completed and produced poor, blocked, or empty output is a RESULT, not a failure. It
> counts as the primary attempt and is reported as-is. A method that blocks its own run is data
> about the method.
>
> Re-running after an infrastructure failure is permitted and the superseded attempt stays in the
> table with its failure reason. Re-running for any other reason is permitted only if every attempt
> is reported and the primary remains the first qualifying one.

**This rule was fixed on 2026-10-17, before any evaluation run existed** (the live project held 0
runs; no evaluation organisation or experiment existed). *Record of the same day:* an earlier draft
written into this file on 2026-10-17 (commit `1c29426`) made attempt 1 the primary result whatever
its outcome; the project owner replaced it the same day, still before any run existed, with the
rule above, which treats an attempt that ended in a listed infrastructure failure as not yet a
result while keeping every outcome of the method itself — `blocked`, `partial`, empty or poor
output — as the result.

*How the rule is applied mechanically, so no attempt is classified by judgement after the fact.*
Every attempt has its own runs row and number (1, 2, …), assigned by the harness at insertion in
start order. When an attempt ends in one of the four listed failures, the harness records which
one on the runs row from the exception that ended it — rate-limit rejection (`LLMRateLimited`),
network error (a transport error reaching a provider or the database), process crash (the worker
died: the run never reached a terminal status and its job was lost), daily-quota exhaustion
(`QuotaExhausted`) — set by the backend, never by a person (`Architecture.md` §9, "Experiment
membership"). Any other ending — `completed`, `partial` (budget cap, `Rules.md` C4), `blocked` by a
gate, `failed` for any other reason — is a result. The primary attempt for a pair is the
lowest-numbered attempt with no infrastructure-failure reason; a pair whose attempts all ended in
infrastructure failure has no primary result and is reported as such, never dropped (A2).

*Memory interaction (Proposed arm).* Later requests for a brand read memory that earlier approved
campaigns wrote (`Memory.md` §6). Only the primary attempt may write back. Primacy is known only
when an attempt ends, and `memory_items` rows can never be deleted (0010, 0011), so the rule is
enforced by the mechanism, not by timing: **memory writes are staged within the run and committed
in one step at termination, only for an attempt that qualifies** (`backend/app/core/budgeted_run.py`,
2026-10-17). Steps append to an in-process `MemoryStage`; it commits in the same transaction that
writes the run's terminal status (for an experiment run, also only if the Phase 10 primacy check
passes inside that transaction), split by what it does to trust (`Memory.md` §6, decided
2026-10-19). **Additions** — new items, reuse bumps — commit only when the run ends `completed`
with a human-approved campaign. **Corrections** — confidence decrements and revocations by decay
— commit at every ending that is not an infrastructure failure: `completed`, `partial`, or
`failed` for any other reason. The rule asks only whether the attempt terminated without
infrastructure failure, so **an attempt that ends `failed` because its commit was refused (no
approved campaign) is still the primary attempt, and its corrections commit**: a reviewer who
edits an item out and rejects the campaign still lowers that item's confidence. An
infrastructure failure discards both; a process that dies takes the stage with it. Nothing
reaches `memory_items` before that point. `backend/tests/db/test_memory_staging.py` stages a write and then ends the attempt with each
of the four infrastructure failures — rate-limit rejection, daily-quota exhaustion, a network
error, and a process crash (a cancelled attempt, and a worker process killed outright) — and
asserts `memory_items` is unchanged each time; controls show a completed run commits, a
budget-stopped run commits nothing, a completed run whose campaign no human approved commits
nothing (its commit is refused and it ends `failed`; `Memory.md` §6.1, as built 2026-10-19), and
status and memory land together or not at all. For corrections, the same file stages a
reviewer's edit-out with each ending: discarded on every infrastructure failure; committed on a
`partial` run, a run `failed` by any other error, a rejected campaign that completes, and a
rejected campaign whose refused commit ends the run `failed` (the decrement is present in
`memory_items`). Once a pair
has its primary attempt, the harness disables write-back for any later attempt of that pair.

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

Test organisations are excluded the same way (migration 0020, 2026-10-17): the fixed E2E
organisations on the live project — and everything in them, including the lasting run fixture
(`Architecture.md` §11, "Live project — permanent footprint") — are marked
`organisations.is_test`, set by the backend only (`backend/scripts/ensure_e2e_users.py` flags the
organisations its own users own). Every evaluation read filters `not o.is_practice and not
o.is_test`; `backend/tests/db/test_test_organisations.py` asserts that a test organisation's runs,
LLM calls and approvals reach none of the reads even when its id is passed in, that the same rows
are counted once the flag is off (so the flag is what excludes them), and that every `*_SQL` read
in `backend/app/evaluation/` carries both filters.

## 4. Metrics

Computed per run from `runs` / `llm_calls`, then aggregated. Baseline is Baseline B unless stated.

**Sector provenance in every table (decided 2026-10-17).** Any run report, table or figure that
names a sector also prints its rules' provenance — `authoritative` or `illustrative`
(`sectors.rules_provenance`, migration 0021). The regulated sector, health supplements, runs
against four **synthetic** rules ("Synthetic rule, not derived from any regulation. Illustrative
only."), so its rows read e.g. "health supplements (regulated; illustrative rules)". A reader must
never be able to mistake an illustrative rule for a regulatory one. What the regulated-sector
results support is the claim that the mechanism enforces the rules it is given; they say nothing
about whether those rules match any regulation (`Rules.md` A6).

| Metric | Formula |
|---|---|
| ARR | `(Calls_baseline − Calls_proposed) / Calls_baseline × 100` |
| SRR | `(Searches_baseline − Searches_proposed) / Searches_baseline × 100` |
| CR  | `(Cost_baseline − Cost_proposed) / Cost_baseline × 100` |
| LR  | `(T_baseline − T_proposed) / T_baseline × 100` — **but see below: T cannot be observed for the web part; LR is reported as counts and DERIVED as time** |

**LR is reported as counts and derived as time — registered 2026-10-28, before any data (owner).** Searches and
pages are replayed from the frozen snapshot, so web time is removed from every arm by construction and
wall-clock latency reduction is unmeasurable. It is NOT fixed with a live, non-replayed run: that would
reintroduce exactly the confound replay exists to remove. Instead: **(a) measured directly, per arm per
request:** searches issued and pages fetched (SRR is the search part of this). **(b) measured separately,
once:** the unit costs, seconds per search and per page fetch at the declared concurrency, taken from the
recording itself, which is real web time under real conditions (the concurrency, the declared engine set and
the politeness delay are reported with them). **(c) derived:** estimated time saving = (count difference) x
(unit cost), reported as a **derivation with its inputs and never as an observed latency**. Model time
(LLM calls, rate-limit waits recorded separately) is not replayed and IS observed; so LR is reported in two
parts, an observed model-time reduction and a derived web-time reduction, and any combined figure is labelled
derived. The reason is written here so that a panel asking "did you measure the speedup or compute it?"
finds the answer already on record: **the web part was computed.**

Reported additionally, broken down by campaign position (1st / 2nd / 3rd per brand), since
that is where the memory effect is expected: depth distribution (D0/D1/D2), memory hit rate,
coverage, reuse depth (`Memory.md` §8). Strong-tier calls per arm are reported as a result
(threat V1, below).

**Memory revocations by kind — named 2026-10-19, before any data exists.** How memory items
died is a result about the memory system, not a schema detail: an item revoked by rule and an
item revoked by a person are different events. Reported for the Proposed arm's evaluation
organisations over the evaluation period, seed items excluded (§3), as three counts:

| Count | Meaning | How it is read |
|---|---|---|
| `decay_validation_flag` | revoked by rule: validation-engine flags lowered its confidence below 0.30 (`Memory.md` §6.3) | `revoked_by` null and the decay record's cause is `validation_flag` |
| `decay_reviewer_edit` | revoked by rule: a reviewer's edit-out lowered its confidence below 0.30 | `revoked_by` null and the cause is `reviewer_edit` |
| `review` | revoked by a person, by choice (`Memory.md` §6.4) | `revoked_by` not null |

"By decay" is the sum of the first two. The split is a property of the row, never a reading of
free text: migration 0024 makes `revoked_by` null if and only if `revoked_reason` is a
well-formed decay record (the confidence before and after, the threshold, the cause — finding
kind or reviewer — and the run), and a person cannot write one. Each decayed item's record says
why that item died. Read by `backend/app/evaluation/memory_revocations.py`; tested in
`backend/tests/db/test_memory_corrections.py`. Zero counts are reported, not omitted.

**Cost is measured in tokens; actual spend is zero.** Every service runs on a free tier
(`Rules.md` §4, C0), so no money is paid for any run. Tokens (input + output, per call, from
`llm_calls`) carry the cost claim. "USD per approved campaign" and CR in USD are computed from
providers' published list prices at the date of the run; **they are list-price-derived figures,
not money paid, and must be labelled that way in the dissertation** (e.g. "list-price-equivalent
USD"). The list prices and their date are recorded with the results.

Tokens are reported **per model tier** as well as in total: model routing (`PRD.md` M6) moves
tokens to cheaper tiers without necessarily reducing them, so its effect is visible in the
per-tier breakdown and in list-price USD, not in the token total alone.

### Free-tier model limitations (stated, 2026-10-14)

Every model runs on the Gemini API free tier (`config/models.yaml`, `Architecture.md` §4.1). Four
consequences are reported as limitations in the dissertation, not hidden:

1. **The strong tier is a Flash model.** No Pro model is available free, so Strong is
   `gemini-3.8-flash` against a Flash-Lite Standard. The capability gap between tiers is much
   smaller than the design assumed, which **weakens the model-routing part of the Phase 7
   contribution**: routing's effect on quality and safety is bounded by a narrow gap, and a null
   routing effect may reflect the free-tier line-up rather than the policy. `Rules.md` T4 still
   routes regulated-sector work to Strong.
2. **Downgrades and deferrals under daily quotas.** Strong allows 20 requests/day. A call that
   would breach a model's daily limit is downgraded with a logged reason, or, if T4 or the
   high-risk rule requires Strong, deferred (`Rules.md` C9). Every downgrade is in
   `llm_calls.route_reason`; the number of downgraded calls per arm is reported, and a run with a
   downgraded call is identifiable in the raw logs. Runs are scheduled so that downgrades are rare
   (`Phases.md` Phase 10), not relied on.
3. **Latency includes free-tier waits.** Low RPM limits make calls wait on `429`s
   (`Rules.md` C10). Rate-limit wait time is recorded separately from model latency, and LR is
   reported both with and without it; only the without-wait figure speaks to the design.

4. **Provider data use.** On the Gemini API free tier, Google may use submitted content to improve
   its products (ai.google.dev pricing page, 2026-10-14). All evaluation data is synthetic or
   pseudonymised (D4), and no PII is sent (`Rules.md` §5), but this is disclosed in the
   dissertation's methodology.

### Threats to validity

**V1 — Model-tier confound between Baseline B and Proposed.** Baseline B uses a single model tier,
and it must be Standard: Strong cannot carry it (about 20 calls per run against 20 requests/day).
Proposed can escalate individual calls to Strong. A quality difference between the two arms could
therefore come from the stronger model rather than from the routing policy, or from adaptivity
generally. This is a **confound**, not only a free-tier limitation.

*Mitigation.*
1. Strong-tier calls are rare in both arms by design. No agent defaults to Strong
   (`Architecture.md` §4.2, `Rules.md` C5); verification and risk assessment run on Standard and
   escalate only where T4 or the high-risk rule requires it (the high-risk rule is defined by what the draft
   copy does, `Rules.md` T9, not by what the product has on file). In the test set that is the regulated
   brand's 3 requests (B07, R19–R21) and the requests on products with entered evidence conflicts, so
   **6 to 8** of the 30 Proposed runs should touch Strong, not a fixed number.
   *Correction, 2026-10-28:* this item first said "at most 6" (the 3 B07 requests plus B01's R01–R03). The
   enumeration from `evaluation/test_set.yaml` is: **regulated (T4): R19, R20, R21 (3); entered conflicts:
   B01 product A and product B, so R01, R02, R03 (3); B03 product A, so R07 and R09 (2; R08 is product B and
   has none).** That is 8. The B03 pair is not certain: its two conflicts (weight 3.23 vs 3.1 kg; warranty 1 vs
   3 years) came from a search-result summary and are marked UNCONFIRMED, not fetched, so whether
   `has_conflicting_evidence` fires depends on what the research finds. The count is therefore **6 (B03's
   conflicts do not materialise) to 8 (they do)**, and the strong-call table below is given for both.
2. Every Strong call is logged per arm in `llm_calls` (`tier`, `route_reason`), so the asymmetry
   is **quantified and reported**, not assumed away.
3. **Strong-tier call counts per arm are reported as a result in their own right**: total, per
   request, and by reason (T4, high-risk, conflicting evidence). Quality comparisons between B and
   Proposed are also reported with the Strong-touching requests separated out, so a difference that
   exists only there is visible as such.

**Strong-tier call budget and the approval schedule (computed 2026-10-28, BEFORE any strong-tier call per run
has been measured).** `gemini-3.8-flash` is 20 calls a day (`config/models.yaml`). *Per run, by arm:* Baseline A
0; Baseline B 0 (single Standard tier, V1); Proposed 0 unless it routes to Strong (T4, conflicting evidence, or
the high-risk rule of `Rules.md` T9, set by what the draft copy cites or attempts, not by what the product has),
in which case **s** calls. **s is not measured**: no generator or router exists and no development call may
reach the strong tier. `Phases.md` plans "at most about 3" a run, a planning figure, not a measurement. The
table is therefore a function of s, with three columns.

| In-window demand (the primary runs and re-runs) | s=3 | s=6 | s=10 |
|---|---|---|---|
| 8 touching runs + 2 re-runs (25%: infrastructure failures only; a T4 call at the cap is deferred, not failed) | 30 | 60 | 100 |
| 6 touching runs + 2 re-runs (B03's conflicts do not materialise) | 24 | 48 | 80 |
| Days at 20/day (8-run case / 6-run case) | 2 / 2 | 3 / 3 | 5 / 4 |

The window of 18 to 23 November is 6 days, 120 calls, so the in-window demand fits in every column. Tuning
(1 regulated request, `Phases.md`) and the 10-campaign calibration (assumed 2 touching campaigns) happen
BEFORE the window and add 3 touching-equivalents: all-in totals are 39 / 78 / 130 calls (8-run case) and
33 / 66 / 110 (6-run case), 2 to 7 days at 20 a day, 2 of them to be spent before the window opens. The
break-even for the window is **(touching runs + 2) x s <= 120**, i.e. s <= 12 at 8 touching runs. The
question that remains open is how many runs the high-risk rule adds: under T9 a run touches Strong only if its
OWN copy cites a restricted claim, and T2 keeps restricted claims out of the generator's view, so this should
be rare; it is measured, not assumed. *The definition in T9 was fixed before s was measured. The earlier
worry (about 21 of 30 runs routing to Strong because 14 of the 20 products carry restricted claims) was an
upper bound under a product-based reading of the rule, never an observation.* Fewer requests would not
help: 20 requests cost the same strong calls unless they drop B07, B01 or B03, and dropping B07 removes the
regulated brand RQ3 needs.

*Measuring s (Phases.md): on the tuning set, before the window. APPROVED 2026-10-28 (owner), with these
conditions, all enforced rather than promised* (`Architecture.md` §4.4, `llm_strong_tier_org_id`):
- the strong tier is enabled for the tuning organisation ONLY, and the gateway refuses a strong call from
  any other organisation (checked on every call, shown to fail when removed);
- every call is logged to `llm_calls` and counted against the 20 a day like any other;
- the tuning organisation is flagged `is_practice`, which the gateway also requires on every call and which
  keeps its runs out of every evaluation read;
- **the calls spent measuring s are reported here and counted in the all-in total** (the 3 touching-equivalents
  for tuning and calibration in the table above are an assumption until then): not yet spent, 0 so far.

The tuning runs are executed on the Proposed arm; for every touching run, the strong calls are counted by
`route_reason` (T4, conflict, high-risk) from `llm_calls`, and s is reported as mean, maximum and per route.
The plan states which column it falls in: s <= 4.5 is the 3 column, <= 8 the 6 column, otherwise the 10 column.

**The coverage gap, stated plainly: the tuning set holds one regulated request, so only the T4 route is
measured.** Its s is reported as MEASURED. The conflict route and the copy-triggered high-risk route are
ASSUMED (3, the `Phases.md` planning figure) unless a tuning request happens to exercise them, and are labelled
assumed wherever they appear. The budget rule is evaluated with the **maximum of the measured value and the
assumption, not the mean**: (touching runs + 2) x max(s measured, s assumed) <= 120. If the other routes turn
out costlier than assumed, that surfaces as a finding when they are first exercised, and the intended
discovery point is before the window, not during it. Until the measurement exists the table above is
a set of scenarios, not a forecast.

**The approval rounds are the real schedule; the approver's hours are the constraint that will slip.** One
approver reviews every campaign (D3). *Reviews:* 90 evaluation campaigns (30 requests x 3 arms) + the
calibration pass (10 practice campaigns; rubric §6 repeats it until agreement is at least 8 of 10, so 1 to 3
passes = 10 to 30 reviews) = **100 to 120 reviews**, and about 10% more if revisions are requested (each
revised campaign is reviewed again): **about 110 to 130**. The tuning runs need no review unless the Proposed
runs there must write memory (up to 10 more). *Time per review:* the rubric boxes it at 10 minutes and quotes
under an hour for 10 calibration campaigns, so about 6 minutes typical, 10 at the cap: **about 11 to 13
hours at 6 minutes, up to 22 at the cap**. This is the approver's time only; the second scorer's calibration
pass and the D2 raters' scoring are other people's hours and are not counted here. *Sittings:* at most 15
reviews a sitting (rubric §2), so the 90 evaluation campaigns need **6 sittings at the least** (about 90
minutes each at 6 minutes, 150 at the cap), plus 1 to 3 calibration sittings before the window. *Serialisation:*
a brand's campaign N+1 is generated only after campaign N is decided (T6, memory write-back), so the 90
campaigns arrive in three waves of 30 (campaign 1 of every brand and arm, then 2, then 3), each wave needing
at least 2 sittings: **6 sittings, in a fixed order that no amount of compute shortens**. At one sitting a day
that is 6 days, the whole 18 to 23 November window with NO slack for a revision, an `undecided`, an
infrastructure re-run or an illness; at two sittings a day (morning and evening; the cap is per sitting, the
fatigue rationale argues against it) it is 3 days plus the overnight generation of each next wave. So the
window is safe only if two sittings a day are acceptable or the window is widened; the calibration and the
tuning measurement must be finished before 18 November. Quota is not the bottleneck; this is.

**V2 — A paraphrase of a restricted claim can pass the claim gate undetected (recorded
2026-10-17).** The gate's matching is deterministic and literal (`backend/app/core/text_match.py`,
`Architecture.md` §8.2): it catches a restricted claim's own wording and registry-prohibited
phrases, case- and punctuation-insensitively, and nothing else. **Deterministic matching cannot
catch a paraphrase of a restricted claim.** Generated statements are labelled from what they cite,
and a factual statement that cites nothing is blocked as unsourced, so the residual risk is narrow
but real: **the classifier (Phase 3 chunk C) extracts a paraphrase of a restricted claim and cites
a verified claim for it**, and the copy passes. **Mocks cannot measure how often that happens** —
a fake model returns whatever the test scripts — so the rate **remains not measured until real
model calls are approved** for that purpose.

*The rubric and the code, stated so they are not read as contradicting each other.*
`docs/approval-rubric.md` T1.3 requires "No `restricted` claim appears in any form, including
paraphrase". **T1.3 is an instruction to the human approver about what to look for, not a claim
about what the code detects: a human can catch a paraphrase; the deterministic gate cannot.** The
rubric is correct as written and is left unchanged (decided 2026-10-17); this threat is where the
code-side limitation is recorded. Until the rate above is measured, paraphrase is caught only by
the approver applying T1.3, and the dissertation must not claim the gate blocks paraphrases
(`Rules.md` A6).

*Mitigation.*
1. Restricted claims are never given to a model: `truth_context`
   (`backend/app/core/claim_classifier.py`, built 2026-10-18) holds verified and deduced claims
   only, it is the only view of a product's claims the classifier sees, and it is the view Phase 6's
   copy writer will get — so a paraphrase has to originate in the model, not be prompted by it.
   A test asserts no restricted or retired claim text reaches the prompt.
2. Every gate block is counted per arm as a result (`backend/app/evaluation/run_outcomes.py`), and
   every approver rejection on T1.3 is recorded with its gate id (`approvals.failed_gate`), so
   paraphrases that reached the approver are counted, not lost.
3. When real calls are approved, the rate is measured on the evaluation set's 33 real restricted
   manufacturer claims (`evaluation/test_set.yaml`) and their paraphrases, and reported with its
   conditions. Until then it is written as *not measured*.

*V2 adversarial existence test — pre-registered 2026-10-18, BEFORE the first call.* Approved by
the project owner: **10 calls, strong tier**, one probe run. Not a sample and not the measurement in
item 3: the coupling V2 names (a generator paraphrases a restricted claim, the classifier
mislabels it) does not exist until Phase 6, and hand-written paraphrases test the classifier
against our imagination of a generator, not against a generator.

- **Design.** Ten paraphrases of the regulated product line's restricted claims (Verdano Whey and
  Verdano Marine Collagen, `evaluation/test_set.yaml`), chosen to be the **hardest we can
  construct**, not representative: each avoids the restricted claim's own wording and every word
  on the superlative list, so only the classifier can catch it, and each either sits inside or
  beside a statement a verified claim truly supports (inviting the model to cite that claim for
  it) or is framed as testimonial or opinion (inviting the model to skip it). The aim is to
  **maximise the chance of a miss**.
- **A miss** is a case whose copy is not blocked although it contains the paraphrase — the model
  omitted the paraphrased statement, or cited a verified claim for it.
- **What may be reported, fixed now.** **n = 10 cannot estimate a miss rate, and no rate will be
  reported from it.** The result is reported only as: *"the classifier was / was not made to miss
  under adversarial construction, n = 10, hand-written"* — and nothing stronger. Every miss is
  reported verbatim: the paraphrase, the restricted claim it carries, what the model returned.
- **Logging.** All calls go to `llm_calls` in a throwaway local database
  (`brandverse_v2_probe`), agent `v2-probe-adversarial`, in a run whose request marks the probe; no
  experiment marker exists on it, so it is outside the evaluation by design (`Architecture.md` §9,
  "Experiment membership"). Calls are added to the tally in `Architecture.md` §4.4. The ledger caps
  the run at 10 calls: a case that needs a repair spends a second call and a later case may go
  unrun — reported as unrun, never re-tried.
- **If it misses**, the classifier is not changed in response: the result goes to the project owner,
  because a classifier that mislabels paraphrased restricted claims may need a design change, not
  a prompt change.
- Script: `backend/scripts/v2_probe.py`; raw results committed to `evaluation/probes/`.

*Outcome, 2026-10-18 02:22–02:29 UTC: no result.* All ten approved calls ended in errors before the
classifier judged anything — eight `400 INVALID_ARGUMENT`, one 90 s timeout, one `503` — so **the
test did not run, and nothing about V2 may be reported from it**, not even the n = 10 statement.
The `400`s are a request-format failure on our side (most likely the classifier's JSON schema —
`$defs`/`$ref`, `additionalProperties`, a nullable `anyOf` — which LiteLLM sends to Gemini
unchanged; unconfirmed without a call). The script did not stop at the first systematic error, so
nine calls were spent that could not succeed. Raw log: `evaluation/probes/v2_adversarial_2026-10-18.json`.

**V2 is deferred to Phase 6 by the project owner's decision on 2026-10-18** — not forgotten. The
strong-tier re-run was refused, reversing that morning's approval deliberately: V2 names a coupling
(a generator paraphrases a restricted claim, the classifier mislabels it) that exists end to end
only once Phase 6's generator produces the paraphrase; hand-written paraphrases test half of it;
and after a failed attempt, ten more strong-tier calls would buy a result already ruled unable to
be a rate. In Phase 6 the measurement in item 3 above is made against real generated output. What
the failed run did yield is bigger than V2: the gateway could not send our structured schema to
Gemini at all, a defect every later structured call (Phase 5's copy writer, Phase 6's agents) would
have hit. Its fix — and an offline test of the request body for every structured schema we send —
was approved instead, with up to 3 cheap-tier calls to confirm it.

*Schema fix, confirmed 2026-10-18 in three cheap-tier calls* (`Architecture.md` §4.4 tally): the
first form still returned `400`; a control showed the environment was fine; the second form
returned a valid reply. **One observation from that valid reply, recorded verbatim and not acted
on** (n = 1, cheap tier, a hand-written sample — it supports no rate and no claim beyond "this
happened once"): the sample copy deliberately contained a statement no listed claim supports,
"Lasts all week." The model returned `{"quote": "Lasts all week.", "claim_id": "60h"}`, citing the
verified claim "Up to 60 hours playback (at 60% volume)" — related, but it does not state "all
week". The gates would pass that statement as verified. This is V2's mechanism (laundering through
a merely related verified claim) on an ordinary rather than a restricted claim. Whether the
classifier needs a design change is the project owner's decision; until then this is reported, and
it is the first thing the Phase 6 measurement should look for. *Decided the same day: not a
classifier defect but an architectural gap, written up as V4 below.*

**V3 — The eight predatory anti-patterns are checked only by the human approver (recorded
2026-10-18).** `Rules.md` T5 requires them to be blocked, not warned. No detector exists for any of
them, so the system blocks none of them, and the gates' outcome table does not list them
(`backend/app/core/gates.py`; `Phases.md` Phase 3, "Specified, not implemented"). In the evaluation
the only check is the approver applying `docs/approval-rubric.md` S2.1 ("None of the 8 predatory
anti-patterns present"), which is an instruction to a person and correct as written. Consequences:
an anti-pattern the approver misses ships in an approved campaign, and its rate is **not
measured**; the dissertation must not claim the system blocks anti-patterns (`Rules.md` A6). Two of
the eight overlap mechanisms that do exist and are reported under them, not under T5: an
unsubstantiated *health* claim is blocked when it matches a restricted claim, a registry prohibited
phrasing (risk gate), or — when the classifier extracts it from the copy (Phase 3 chunk C, built
2026-10-18 and tested with a mocked model only) — cites nothing (claim gate); fabricated *social
proof* is blocked only in that last way, when extracted as a factual statement that cites nothing,
and how often a real model extracts it is not measured. Approver rejections on S2.1 are counted per
arm (`approvals.failed_gate`).

**V4 — Citation does not imply entailment (recorded 2026-10-18).** The claim gate's citation check
confirms that a cited claim **exists, belongs to the product, is active and is verified**. It does
not confirm that the claim **entails the statement citing it**. A statement can cite a genuine,
active, verified claim and still overstate it, and nothing in the design catches that. This
follows from the definition, not from observation: whether a cited claim exists and is active,
and whether it supports the statement, are different questions, so by construction a check that
answers the first cannot detect a failure of the second. It therefore holds for any design that
grounds generated text by attaching a citation to each statement and checking only that the
citation resolves; it is not a bug in this code. The example below shows the gap is reachable; it
is an illustration, not the evidence. It belongs in the dissertation's limitations, with the
example attached.

*How it was found* (2026-10-18, verbatim). Not constructed for this write-up: it surfaced in the
third of three cheap-tier calls (`gemini-3.1-flash-lite`) made to confirm the structured-output
schema fix (`backend/scripts/schema_confirm.py`, the `classifier` variant; logged to the local
throwaway database, `Architecture.md` §4.4 tally, 2026-10-18 02:58–11:07). The sample copy given
to the claim classifier was:

> Tidewell Loop N2: built for the monsoon. IPX7 rating keeps the rain out. 10 mm drivers bring
> every word through. Lasts all week.

with three verified claims: `ipx7` "IPX7 rating", `drivers` "10 mm drivers", and `60h` "Up to 60
hours playback (at 60% volume)". "Lasts all week." was put in deliberately as a statement no claim
supports. The model returned `{"quote": "Lasts all week.", "claim_id": "60h"}`.

*The numbers.* The claim says up to 60 hours, which is 2.5 days, and only at 60% volume. "All week"
is 7 days, 168 hours: 2.8 times the claim's best case, with its condition dropped. The citation
resolves — `60h` exists, is active and is verified — so **the claim gate passed the statement,
correctly by its own rules**. The gate resolves citations; it was never asked whether the claim
says what the statement says. One instance, cheap tier, hand-written copy: it shows the gap is
reachable, not how often it is reached. V2 (a paraphrase of a *restricted* claim passing as
verified) is one case of this general gap.

*Partial mitigation (mechanical, no model calls; built 2026-10-18, `backend/app/core/quantities.py`,
finding `unrepeated_quantity`).* When a cited claim contains a quantity (a number written in
digits, with its unit if it has one) and the citing statement does not repeat it, the claim gate
**flags** the statement for the reviewer. Flag, not block. Every quantity in the claim must be
repeated, units compared across spellings ("60h" repeats "60 hours"). With the claim "Up to 60
hours playback (at 60% volume)": "Lasts all week." is flagged (both quantities dropped); "Lasts up
to 60 hours." is flagged too (the 60% condition dropped); "Lasts up to 60 hours at 60% volume."
passes; a statement citing a claim with no number in it is untouched. **What it catches: numeric
overstatement only.** It does nothing for "clinically shown" cited to a single small study, or
"loved by thousands" cited to a review count of forty. Those stay with the approver
(`docs/approval-rubric.md`), as V3's anti-patterns do. Nor does it check qualifiers written in
words: "Lasts 60 hours." citing "Up to 60 hours playback" repeats the number and passes, though
"up to" is gone. Number words are not read, so a statement restating the quantity another way
("two and a half days of playback") is flagged and the reviewer clears it. A flag is a prompt to a
person, not a finding: flags are counted per arm and reported as flags, never as a count of
overstatements.

*Considered and rejected: a model-based entailment check.* Asking a model, per statement, whether
the cited claim entails the statement would cover the non-numeric cases. It was rejected for three
reasons: it adds a call for every statement, on the highest-volume path in the system; with the
strong tier at 20 calls a day it cannot run at that volume; and the gate would then depend on the
same class of model it exists to constrain, so the check meant to catch a model's overstatement
would itself be a model's judgement.

**V5 — The authority prior is the developer's untuned judgement (recorded 2026-10-25).** A
page's source score includes an authority factor read from its host: 0.95 for government and
regulator hosts, 0.85 for academic, 0.25 for social and user-generated, 0.50 for every other host
(`config/scoring.yaml` `source:`). The classes, the suffixes in each and the four values were
chosen by the developer. **There is no citation behind the specific values and no calibration.**
The score feeds `E`, which feeds `M`. *Mitigation: the evaluation must not depend on it.* When
Phase 10 runs, the headline result is reported **twice**: with the authority prior as configured,
and with it flattened to 0.5 for every host. These two settings are the whole sweep and are fixed
now, before any data. If the comparison between arms survives flattening, the prior is not
carrying the claim, and one sentence says so; if it does not survive, that is a finding to have
before a viva, not during one. (Flattening changes only the authority factor; recency and
specificity are unchanged, and `E` is still the maximum across an item's sources.)

**V6 — The source model conflates authority and independence (recorded 2026-10-25; ruled and
restated 2026-10-26).** The scorer has one axis where two are needed. A brand's own domain is
**high authority** (does this source know?) and **zero independence** (does it have an interest in
the answer?) about its own product, and the two are different properties. A manufacturer is the best
possible source for its product's dimensions, materials and ingredients, often the only one, and has
the maximum possible interest in claims about its performance. The model gives such a host the
`unknown` prior, 0.50, above the 0.25 of forum posts: wrong in both directions at once.

*Excluding first-party sources would be wrong*: for "the kadai is 3 mm thick" the brand's own site
is the correct evidence and nobody else holds it. **The rule (owner, 2026-10-26):** a first-party
source is acceptable evidence for a *factual property* of the brand's own product; it is **not**
acceptable as the *sole* evidence for a *comparative or efficacy* claim. The tests already exist: a
claim that trips the superlative list is comparative by construction, and a claim in a regulated
sector carries efficacy weight by construction.

*The check asked for at Phase 6 is answered early (2026-10-26): yes, by two paths.* (1) `deduced`
claims: write-back refuses them today, unconditionally; once the Phase 6 caller wires `source_score`
in, a deduced claim resting on the brand's own page passes on score alone (about 0.73 for a matching
page that states no date). (2) `verified` claims: **live now.** `product_claims.evidence_url` has no
host check (0017 requires only a URL), so a claim verified against the brand's own domain is
unmarked and indistinguishable from independent evidence, and is eligible for write-back if the gate
resolves it. This is a live gap, not a future one.

*It cannot be a scoring tweak.* Nothing in the system records a brand's own domain (`brands` holds
name, voice, embedding and sector), so the fix needs new data: a brand domain. Order: Phase 5, the
search pipeline carries a `first_party` marker on every result, unpopulated and not scored on; Phase
6, with the domain field, the rule above and an independence factor separate from authority. The
independence factor is not designed here. (`Memory.md` §3.1; `Phases.md` Phases 5 and 6.)

**V7 — The evaluation's searches can only return evidence about a product's CATEGORY, never about the
product (recorded 2026-10-28; owner).** The test set's brands and products are pseudonyms, which no search
engine can resolve, so the query planner takes the product's generic category and never a brand or product
name. That is forced, and it is correct, and the consequence is larger than a note. **Facet 3 exists to
find independent reference facts for a product's attributes, and under pseudonyms it can only ever return
generic category facts.** So the claim-verification machinery is evaluated on evidence that cannot, in
principle, verify a specific product's claims. This bounds what the evaluation can show about claim
grounding: it can show that the pipeline finds, scores, stores and reuses category-level evidence and that
the gates treat claims correctly given what they are handed; it cannot show that the system finds
independent evidence for a particular product's specifications. The same applies to the competitor and
audience facets, which describe a category and its buyers, not the pseudonymous brand. (`Memory.md`;
`backend/app/research/planner.py`; related V6, which concerns whose evidence a page is, where V7 concerns
what the search could ever find.)

**V8 — The frozen snapshot has no off-device copy, so reproducibility depends on one disk (recorded
2026-10-28; owner's choice).** The evaluation's arms reason over a frozen copy of the web (search results and
page bodies) because the web cannot be re-captured later: the same queries a month on return different
pages, and some of today's pages will be gone. The only backup of that snapshot is a folder on the SAME
physical disk as the snapshot. It protects against accidental deletion or corruption of the store and not
against disk failure. **If that disk fails, the snapshot is unrecoverable and the evaluation's results become
unreproducible**: the stored findings, scores and per-arm outputs would remain, but a reader could not replay
the arms over the evidence they saw, and a re-run would meet a different web. The thesis states this; it does
not describe the snapshot as backed up off-device. Mitigations that exist: every committed request has a
hashed manifest line in the repository (so what was captured is checkable, not recoverable); the backup
certificate records `same_disk: true`; and the condition closes the day an external drive is attached and the
same tool is run. The retention condition of the zero-retention exception (`Architecture.md` section 6) is
therefore PARTIALLY MET.

### A defect found and fixed: a throttled search was indistinguishable from no evidence — 2026-10-26

*Infrastructure failure masquerading as a system result.* The search provider was written against a
mock that was the developer's own model of SearXNG. Run against the real engine for the first time,
it showed a case the mock did not contain: when SearXNG's upstream engines are throttled (brave and
wikipedia answering 429, duckduckgo showing a CAPTCHA) it answers **HTTP 200 with an empty result
list**, naming the failed engines in a separate `unresponsive_engines` field. The provider read only
`results`, so it returned an empty list, which is also exactly what a search for something that does
not exist on the web returns. The two were the same value.

*What it would have caused.* Every consumer downstream would have read a throttled search as a
finding about the world: the topic has no web evidence. Research would have staged nothing for it; a
claim that needed that evidence would have been unsupported, and the claim gate would have blocked or
flagged the claim *for lack of evidence* when the truth was that the laptop's IP had been throttled.
In the evaluation, an arm's quality would then have varied with how recently the harness had searched,
not with the system under test, and a run that hit the throttle would have looked like a run that
found nothing. Nothing in the output would have said which. The real engine began throttling this
laptop after about ten searches in a few minutes and had not recovered twenty-five minutes later
(`docs/searxng.md`), so this was the normal condition of the live engine under load, not a corner.

*Fixed.* Zero results with any engine named as failed is refused (`SearchRateLimited` if a reason says
"too many requests", else `SearchUnavailable`), so it surfaces as a failed search and degrades to a
partial result (`Rules.md` C4) instead of an absence. The same defect has a second form, now also
closed: a search that returns results while some engines failed is a *degraded* result set, and the
failed engines and their reasons are carried through the search record into the run report
(`SearchEvidenceReport.degraded_searches`, `failed_engine_counts`, `engines_seen`), so that a run that
saw one engine can be told from a run that saw three. Tests pin both.

*What it means for the write-up.* The search layer's failures must be reported separately from the
system's results: a run's evidence is only as complete as the engines that answered, and the report
says which did. The defect was found because the provider was exercised against the real engine
rather than the mock; the mock now has the real answer's shape, and a real response has still to be
recorded as a fixture (`docs/searxng.md`, "Not verified").

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

### The relevance set — decided 2026-10-18, before it is built or any ranker is tuned

**Labeller: Kundan, by hand, blind, before any tuning.** The developer does not label the set the
developer's retrieval is judged against, and no model labels it (the system would be measured
against another model's opinion, a confound every number would carry).

- **Pool.** 50 queries, each with a fixed candidate pool drawn from the **union** of lexical
  (full-text) and vector retrieval, so the pool is not shaped by the ranker under test (RRF and the
  `M`-score are not used to build it).
- **Blind.** Candidate order randomised per query; no scores, no ranks, no indication of which
  retriever found a candidate.
- **Judgments.** Three, each defined in one sentence in the task itself: relevant, partially
  relevant, not relevant.
- **Consistency.** 5 of the queries appear twice, unmarked; the labeller's agreement with themself
  on those is **reported**, not hidden.
- **Frozen.** Labels are timestamped and frozen before any ranker tuning. If tuning later suggests
  a label was wrong, **the label stands** and the disagreement is reported.
- Precision@10 (`Memory.md` §8) is computed against these labels only.
- **Retrieval depth is a reported parameter.** Every retrieval logs the `hnsw.ef_search` in effect
  (`Memory.md` §4.3). **Every Precision@10 figure states both the `ef_search` used and the number
  of rows in the collection searched**: a retrieval number without the collection size cannot be
  interpreted.
- **The `ef_search` sweep, pre-registered 2026-10-18** (fixed before any measurement, on the same
  terms as the attempt rule in §2 and the superlative tiers): Precision@10 is measured at
  `ef_search` = **40, 100, 200 and 400**, on the same frozen labels and corpus, and all four are
  reported. **Expected result, stated in advance: flat.** The Phase 4 relevance corpus (three
  fictional brands, `evaluation/relevance/brief.md`; separate from the seed memory, which never
  reaches evaluation, `Memory.md` §7.1) is planned at the seed memory's scale: about 40 items per
  brand, about 120 rows. The exact count is recorded when the corpus is frozen. HNSW at
  `ef_search` = 100 over 120 rows is close to
  an exhaustive search, so the index approximates almost nothing and Precision@10 is expected to
  be identical, or nearly so, at all four values. A flat result confirms the measurement works. It
  says nothing about the parameter's effect at scale, and it will not be reported as a finding
  about `ef_search`.
- **Future work, not started:** a sweep that can show `ef_search` mattering needs a corpus large
  enough for the index to approximate: thousands to tens of thousands of rows, with labels to
  match. That is far larger than Phase 4 and is recorded as future work, not scheduled.

### Limitation of the relevance set: how the 50 queries were written — recorded 2026-10-25

**The 50 queries were drafted with AI assistance, from `brief.md` alone, and then reviewed by
Kundan, who read all 50 on 2026-10-25 and accepted them without changes.** The AI never saw the corpus: it did not exist when the queries were written.
They are therefore not purely the labeller's own unaided wording, and every Precision@10 figure
computed on this set carries that fact. Stated plainly, as a limitation of the set:

- *What it does not change.* The labels are still given by Kundan by hand, blind, and no model
  labels anything (the rule above stands). The queries were written without sight of the corpus,
  which is the independence the blind design needs from them.
- *What it may change, direction unknown.* Queries drafted by a language model can share regularities
  of phrasing (length, vocabulary, the way a question is shaped) that a set written unaided would not,
  and a retrieval encoder is itself a trained language model. Whether that makes this set easier or
  harder for the pinned encoder than a human-written one would be is not measured, and no
  adjustment for it is made. Kundan's review reduces the risk and cannot be shown to
  remove it; it changed no query, so the wording is the AI draft as written.
- *What the review did.* Kundan read all 50 queries on 2026-10-25 and accepted them without
  changes. No query was edited, so there is no edit history to record; the set as frozen is the
  AI draft.

**Order of events — an incident, recorded 2026-10-25.** The protocol (brief, then corpus, then
queries) exists so the corpus cannot be shaped by the queries. On 2026-10-25 the order was
reversed: the queries were supplied and frozen first, and a first corpus was drafted from the brief
with the queries in view. The owner's instruction caused the reversal; the drafter noticed it and
disclosed it. It was caught before any pool was shown to the labeller and before any label
existed. Disclosure alone does not repair it, so the corpus was **redrafted blind**: by a separate
session that was given `brief.md` and nothing else, and instructed in writing not to open any other
file, in particular the queries and the pool. The surviving corpus is that one. Detail and
figures are in the next subsection.

### The corpus as frozen, and the incident behind it — 2026-10-25

**What is frozen.** `evaluation/relevance/corpus.yaml`: 120 items (40 each for Mettlepan, Zarvik
Nutrition and Nookwell Home), hashed in `corpus.lock` after `queries.lock` and before the pool
(`pool.lock`). `corpus.lock` records that it supersedes the first corpus and why. Each item is one
fact in the form of a memory item, 39 to 60 encoder tokens against a cap of 400, and passes
write-back's own screens for prices, discounts, dates, promo codes and personal data
(`tests/test_relevance_corpus.py`). Counts by kind: claim 30, insight 24, creative 23, persona 15,
competitor 15, sector 13.

**The first corpus** (commit `d5b880d`; kept in the tree under
`evaluation/relevance/superseded/reversed-order/` with its lock and the pool built from it, as
evidence of what happened and not for use). It was drafted by the same coding assistant that
froze the queries, from the brief, with the 50 queries in view.

**The redraft.** A fresh subagent session with an isolated working directory holding a copy of
`brief.md` only. Its instructions named the constraint explicitly (read nothing outside that
directory, follow no path the brief mentions, report every path opened) and did not mention the
queries' content, the pool, the unanswerable queries, the claim gate, or any particular
claim-sensitive topic. Its report lists two paths, `brief.md` and its own output. Its transcript
was not retained, so what it read cannot be audited from its tool calls. **The blinding is
evidenced by measurement instead, and the measurement is better evidence than a transcript would
have been**: a transcript would show only which files were opened, not whether the writing was
shaped by what was seen. A session that had read the queries would be expected to write the way
the first, query-aware drafting did, with a best-item coverage near its 0.835. This one landed at
0.639, with the difference clearly separated from zero (below), and the phrase measure points the
same way. That is consistent with a session that never saw the queries. It is not proof of it:
0.639 is not zero, the two corpora differ in session and style as well as in blinding, and this
is one comparison on 42 queries. The
Zarvik sector items were written from the brief's note that the brand is in a regulated sector
(plus the drafter's own general knowledge of supplement marketing norms); the sector items for
Mettlepan and Nookwell were invented by the drafter, as general norms and not legal citations.
The brands, facts and behavioural statements ('posts showing X were saved more often') are all
invented; none is evidence about real customers or creative, and nothing in the thesis may cite
them as such.

**The count first: at least 3 of the 42 answerable queries (q27, q13, q42) had dedicated
supporting items in the first corpus and none in the blind one.** (For q13 and q42 the blind
corpus still holds general voice items; what it lacks is anything on exclamation marks or
formality.) The three are not equally diagnostic, and the next subsection says how. The aggregate
figures follow.

**Echo, measured on both corpora** (`evaluation/relevance/echo.json`; definitions, stop-word list
and bootstrap seed fixed in `app/evaluation/echo.py` before either corpus was measured). Echo is
how much of a query's own wording a corpus repeats. For the 42 answerable queries (mean per query):

| Measure | First corpus (queries in view) | Redrafted blind | First − blind, 95% interval |
|---|---|---|---|
| Best single item's coverage of the query's terms, over the whole corpus (no retriever involved) | 0.835 | 0.639 | **+0.197** (0.132 to 0.269) |
| Share of the query's adjacent term pairs found adjacent in a pooled candidate | 0.055 | 0.037 | **+0.018** (0.008 to 0.028) |
| Share of the query's terms found in a pooled candidate | 0.175 | 0.158 | +0.017 (−0.002 to 0.035) |

For the 8 unanswerable queries the same three measures are 0.356, 0.014 and 0.096 (first corpus)
and 0.402, 0.011 and 0.126 (blind): no sign of echo, as expected for queries the first corpus was
checked not to answer.

Read these for what they are. The first corpus echoes the queries more, clearly so on the one
measure no retriever touches, and a little on phrases; on the share of query words in a pooled
candidate the interval includes zero. That is the effect of the reversed order on *this* pair of
corpora, with 42 queries. It is not proof that the blind corpus is free of echo: its best-item
coverage is still 0.64, because both are written from one brief and share its brand and product
words, and the interval does not separate the order from the other ways the two corpora differ
(two sessions, different item style and length). The pool measures are partly mechanical, since
BM25 puts items that share words into the pool. The two corpora are not pooled together: only the
blind corpus is labelled.

**The checks, repeated on the blind corpus.**
- *The eight unanswerable queries (q43 to q50).* For each, the four top candidates of BM25 and of the
  vector ranker were read, and every item containing a term an answer would need was read in full.
  None answers any of the eight. Items that touch the topic exist: a competitor's frequent sales
  (`mp-08`), creators who 'convert better' (`mp-30`, Mettlepan, unquantified), three items that
  mention returns only as a reason to keep listings accurate (`mp-40`, `nw-31`, `nw-39`), the
  move-in bundle's contents (`nw-26`), quick-commerce listing design (`zv-14`, `zv-34`). A labeller
  may judge some of them 'partial'; the eight are reported separately for that reason.
  `tests/test_relevance_corpus.py` holds each reading as a reviewed exception and fails on any new
  or stale one.
- *The claim-sensitive queries (q27, q28).* q28 (magnesium powder and sleep) has material that
  bears directly on it: the powder's description and the statement that it is not a treatment for
  sleeplessness (`zv-23`), the rule against implying better sleep (`zv-37`), the medical-advice
  limit (`zv-38`), the general ban on disease claims (`zv-35`). The corpus has no item written as a
  trap: `zv-19` (a 'broader amino acid profile') and `zv-23` (contributes to normal magnesium
  intake) would overstate if cited loosely for recovery and for sleep, but they are ordinary brand
  material (stated in `Phases.md`, Phase 3, as it bears on the claim gate).
- *A third kind of query, not two, and three cases that are not equally diagnostic.* The eight
  (q43 to q50) were **designed** unanswerable. The other 42 were written to be answerable, by a
  labeller who had not seen the corpus. Comparing the two corpora query by query finds at least
  three that had dedicated supporting items in the first corpus and none in the blind one. Q27 is
  evidence of what the reversed order did. For q13 and q42 the comparison cannot say. They are
  reported as their own category, neither folded into the eight nor dropped, and none is edited
  into the corpus.
  - *q27 (what can we say about plant protein and recovery): strong evidence.* The first corpus
    held three specific artifacts for it: the evidence file (one study of lean mass and strength,
    which did not measure recovery), the permitted wording, and a past rejection of 'helps you
    recover faster' (`zv-26`, `zv-27`, `zv-28` in that corpus's numbering, under
    `superseded/reversed-order/`). The blind corpus holds none of the three. It has boundaries
    (`zv-35`: wording about recovery 'has to stay within permitted nutritional descriptions';
    `zv-27`: no claim that plant protein beats whey) and the blend's amino acid profile
    (`zv-19`), and no item says what may be said about recovery. No brand's standing knowledge
    spontaneously contains a record of a rejection about recovery unless someone was writing
    toward that question, so these three existed in the first corpus because the query asked for
    them. A reader can check it against the superseded file, and it is better evidence of what the
    reversed order was doing than the aggregate figures are.
  - *q13 and q42 (how formal are we, and do we use exclamation marks / how casual): ambiguous.*
    The first corpus had a dedicated item for each (`mp-31`, `nw-27` in its numbering: 'exclamation
    marks are rare'; 'fine ... no more than one a post'); the blind corpus has nothing on
    exclamation marks or formality, and the best item's coverage of their terms falls from 1.00 to
    0.00 (q42) and from 0.67 to 0.00 (q13). **Two readings fit, and they cannot be separated from
    here:** (a) the first corpus invented those items because the queries asked; or (b) the blind
    corpus simply omitted voice material that a real brand's memory would plausibly hold, since a
    note on exclamation marks and formality is ordinary standing voice knowledge. Either could be
    true of either brand. Claiming (a) for all three would overstate what the comparison shows;
    it is shown for q27 and not for these. The blind voice items (`mp-12`, `nw-12`) bear on the
    'casual' half, so these two are at most half unanswerable.
  - *Not a claim of completeness.* The three come from the query-by-query comparison and from the
    drafter reading each answerable query's top candidates. The reading is the drafter's judgement,
    not the labeller's, and it does not say no other query is thin; the labels will show that.
    Q27 is not 'a category of one': it is the clearest of at least three.
  - *Expected behaviour and provenance.* For q27 the right behaviour is the same as for the eight:
    return nothing, or only items below the reuse band (`Memory.md` §3.2). For q13 and q42, the
    voice items only. The provenance differs, and the thesis says so: the eight are unanswerable
    by design; q27 is unanswerable because the brand's own standing knowledge, written without
    sight of the question, does not contain the answer; q13 and q42 are unanswerable in part for
    a reason that is either that or a gap in the blind drafting, and which is not known. Nothing
    changes about the labelling: the labeller labels blind and is not told which query is which.
- *Accents.* No accented character in any file under `evaluation/` (`Memory.md` §5).

**The pool.** `pool.lock`: the union of the top 15 of BM25 and of the pinned encoder's cosine
ranking per query (neither is the hybrid ranker or the M-score), 55 sittings (50 queries and 5
hidden repeats), 1,340 judgments, seed 20261025.

**Who writes what, and in what order (decided 2026-10-18).**

1. The developer writes a one-page **brief**: three fictional brands, their sectors, product lines
   and rough positioning (`evaluation/relevance/brief.md`). The labeller sees the brief.
2. The developer drafts the **corpus** from the brief, then freezes and hashes it. *(Amended
   2026-10-25: the surviving corpus was drafted by a separate session given the brief only; see
   the incident above.)* **The labeller
   never reads the corpus text before labelling.**
3. The labeller (Kundan) writes the **50 queries from the brief only** — plausible marketing
   questions about brands whose shape is known, not questions copied from documents.
   *(Amended 2026-10-25: the queries were drafted with AI assistance and reviewed by Kundan,
   who accepted them without changes; see the limitation above.)*
4. **8 of the 50 are unanswerable by design**: written from the brief to have no good answer in the
   corpus. They are reported separately from the 42 answerable ones. Without them precision is
   measured only on answerable queries, and a retriever that always returns ten items looks
   competent when it is not. On the 8, the right behaviour is to return nothing, or only items that
   score below the reuse band (`Memory.md` §3.2: `M` < 0.65, "perform fresh analysis"); that is a
   result the `M`-score is judged on.
5. Then the pool is built, and the labeller labels it blind (above).

**Embeddings — decided 2026-10-18.** Computed locally by a pinned sentence-transformer
(`BAAI/bge-base-en-v1.5`, revision and weights hash in `Memory.md` §5), never by a hosted API and
never billed: a recorded revision and a weights hash checked on load, which a hosted endpoint
does not give; no quota competition with the strong tier, and not the
contribution — the encoder is a parameter of the retrieval results and is reported as such.

### The first labelling sitting, 2026-10-25: export discarded, Precision@10 not reported

A labelling sitting was attempted on 2026-10-25 and its export failed validity checks. The
export was discarded; it is not committed, was not frozen, and **no figure was computed from it**.
The diagnostics, which are the record:

- **Coverage:** 1,005 of 1,340 judgments; 41 of 55 sittings complete, 14 untouched, none partial.
- **One value:** 1,003 of 1,005 (99.8%) were "relevant", 2 "partial", 0 "not relevant".
- **Unanswerable queries:** 120 candidates across five queries verified to have no answer in the
  corpus (q43, q46, q48, q49, q50) were marked "relevant".
- **Speed:** the median interval between consecutive judgments was 0.89 s (p10 0.60 s, p90
  2.53 s); 57% of intervals were 1 s or less.
- **Repeats:** 4 of the 5 hidden repeats were labelled; 99 of 99 pairs agreed, which is
  uninformative because every one of the 198 judgments was "relevant".

*Page defect found in review.* `label.html` had no default-selected judgment, but keys 1/2/3
judged and advanced in one action and did not ignore auto-repeat or modifier chords, so a held key
would have labelled the rest of a sitting. The recorded intervals (minimum 0.44 s) are too slow
for auto-repeat, so this defect is not shown to have caused the sitting's result; the cause is not
established. The page now ignores repeated keydowns and chords (`label_page.html` and the derived
`label.html`; a test pins it). Rapid repeated presses of "1" remain possible by design.

**Consequence:** Precision@10 on the relevance set is **not reported**. The protocol artifacts
(queries, corpus, pool, locks, the superseded corpus, `label.html`, the freeze script) are kept as
the evidence the protocol was real and so a later sitting can still be run on the same pool.

**Validity refusals in the freeze script — decided 2026-10-25, before any labelling is
reattempted.** `scripts/freeze_relevance_labels.py` already refused an incomplete export. It now
also refuses an invalid one, so that no export is frozen only because nobody thought to check it.
Three thresholds, each a hard refusal with **no override flag**: a legitimate export that trips one
is a conversation, not a command-line switch.

| Refuse if | Threshold | Why |
|---|---|---|
| A candidate under a designed-unanswerable query (q43–q50) is marked "relevant" | any one | Objective: the finished corpus was verified to contain nothing that answers them, so such a label contradicts the repo. It is not a judgement call. |
| One label value exceeds a share of all judgments | 90% | A pooled candidate set is built to contain non-matches; a near-uniform set did not read them. |
| The median interval between consecutive judgments is under | 2 seconds | Reading a candidate against its query takes longer than that. Intervals are the gaps between consecutive judgments over the whole session, ordered by timestamp. |

The checks run on whatever was judged, so a partial export is also tested. On **any** refusal the
script prints the same five diagnostics: the label distribution (counts and shares); the labels
under each unanswerable query; which sittings are complete, partial and untouched; the interval
distribution (min, p10, p25, median, p75, p90, p99, max, share at or under 1 s); and the hidden
repeats' agreement, with the note that a uniform label set agrees with itself perfectly, so that
figure means nothing until the distribution is read. Run against the discarded 2026-10-25 export
(read-only; nothing written), it refuses on all three: 144 "relevant" labels under the unanswerable
sittings (q43, q46, q48, q49, q50 and the q50 repeat; 120 on the five distinct queries), 99.8%
"relevant", and a 0.89 s median.

The thresholds are judgement-set limits, not measured ones. 90% and 2 s are deliberately loose,
chosen to catch a set that was not read and not to police a fast, honest labeller; they were fixed
before any new label exists, and are not to be tuned afterwards to admit a particular export.

### The M-score's bands are provisional — recorded 2026-10-25

The reuse bands (0.90 / 0.80 / 0.65) and the write-back gate at `M >= 0.90` have never been
calibrated against a six-component `M`; they are applied today to a four-component one (`S`, `P`,
`A`, `F` renormalised over 0.75), because `C` and `E` do not exist yet. When they arrive identical
items score lower, so the thresholds mean something different. Therefore:

- The current bands are **provisional**.
- **Band tuning, and the approver's 10-campaign calibration to >= 8/10 (D3, rubric §6), happen only
  after `E` and `C` exist.** The practice campaigns are produced by a system whose reuse decisions
  depend on the bands, so calibrating before they are fixed would calibrate a system that will not
  be the one evaluated.
- **No figure derived from a four-component `M` is reported anywhere.**
- Audit: `score()` has no caller outside the tests, no real `M` has ever been computed, and no
  recorded figure depends on `E`. This was found by searching by pattern, not by reading every
  line. Details: `Memory.md` §3.2.

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
  real manufacturer copy the claim gate must block, so they test T2 and T3 against real input. (Not
  T5: no anti-pattern detector exists — threat V3.)
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

### Web evidence during evaluation: do runs use the search cache? — RULED 2026-10-25: option 3

**The problem.** If arm A runs on Monday and arm C on Wednesday against one cache, they saw
different web snapshots and the comparison carries that difference silently. A normal TTL cache
makes it worse: which arm gets reused evidence, and how old, depends on run order and timing, not
on the arm. The cache (`app/research/search_cache.py`) records the mode, the TTL in force, and for
every search whether it was fetched or reused and the age of the entry used, so the difference
would at least be *visible* in the report. Visible is not controlled.

**Options.**

| Option | What the arms see | Why it fails or holds |
|---|---|---|
| 1. Shared TTL cache (the development behaviour) | Evidence whose freshness and reuse depend on run order | Confounds arm with time and order. Rejected for evaluation. |
| 2. Bypass the cache | Every search is live | Identical queries from two arms still see different web states, and now nothing can be re-examined afterwards. Also puts the full search load on upstream engines, which rate-limit. Controls nothing it claims to. |
| 3. **One frozen snapshot, recorded then replayed** | Identical results for an identical normalised query, whichever arm asks | Removes the time difference where queries coincide; see the residual below. |

**Ruling: option 3** (2026-10-25, project owner), with the window defined per request, not
globally. Evaluation runs use a dedicated snapshot namespace in which an entry is written on the
first miss and **never expires and is never overwritten** for the duration of the evaluation; the
TTL does not apply to it. Every arm that issues the same normalised query gets the same results.
~~A query no earlier arm issued (the planner's queries differ by arm) is fetched live once and
recorded, so later arms issuing it see the same thing.~~ **Superseded 2026-10-26** by the ruling
below, which has the planner run once per request, so no arm issues a query another did not. The D0
arm searches nothing and is unaffected.

*The window is per request.* The comparison is between arms on the same request, so what must be
held constant is that the arms of one request (R07's three, say) saw the same web, not that R01 and
R30 did. **All arms for a request run within N = 3 days of that request's first fetch.** A global
window would also break the first time a re-run under the attempt rule ran past it, and a broken
rule gets quietly ignored. This one is enforceable: the harness **refuses to run an arm whose
request's snapshot is older than N days**, rather than leaving it a target someone remembers.
(A request whose searches were all replays has no first fetch of its own and no constraint: its
arms necessarily saw the same results.)

*Reported, per request:* the actual span between its first and last fetch, and the share of its
evidence replayed versus fetched live, **and the age of the oldest replayed entry**: a request
running entirely on three-week-old snapshots is comparable across arms and also stale in absolute
terms, and the second fact must be visible rather than implied by its absence. *Reported once, descriptively, not as a constraint:* the
global span of the snapshot, earliest to latest fetch.

*Residual, stated now.* A query first issued late in the evaluation was fetched later than one
issued early, so the snapshot spans a date range, not a single instant. Option 3 narrows the
confound; it does not remove it, and the dissertation says so.

*Storage and retention.* The snapshot is exported to a file after the evaluation. **It is not in
the repository**, because it holds third-party result snippets; the repository holds only its
SHA-256 and its date range. **It is retained, on the laptop and on one backup copy, for as long as
the thesis stands**: a reproducibility claim depends on the file existing, and keeping only the
hash would mean nobody can ever check it. Its location on the laptop and the backup is recorded
here when the file is first written: *laptop: not yet written; backup: not yet written.*

*Consequences.* (a) A `snapshot` cache mode is built (write-once, no expiry, namespaced, each entry
tagged with the request that first fetched it); until then the cache has only `use` and `bypass`.
(b) The harness refuses to start an evaluation run in any other mode, and refuses an arm outside
its request's N-day window, as it refuses a tuning brand in the evaluation set (D1). (c)
Development, tuning and calibration runs use the ordinary TTL cache.

### Search is held constant across the arms of a request — RULED 2026-10-26

**Why (attribution).** If the arms issued different queries, any difference in their outputs could
come from the memory mechanism or from having seen different evidence, and the comparison could not
say which. The thesis claims a contribution from memory and adaptive depth; the comparison has to
isolate exactly that. **Rule:** all arms of a request use the same query set and the same recorded
snapshot, replayed for each. **The query planner runs once per request, not once per arm**, and its
queries are fixed when the request is recorded.

**Consequence (cost).** The recording needs `S = 30 × q` searches, not `30 × 3 × q`: 90 rather than
270 at three queries per request. The saving follows from the rule and is not the reason for it.

**`q` is a parameter, not a constant.** The number of queries per request is configured
(`research` in `config/scoring.yaml`, when the planner is built) and **reported beside every result**.
It is not fixed tonight and the throttle does not design the experiment: it is set from the measured
search rate once the probe has run (below), and the rate informs the choice rather than dictating
the experiment.

**Depths cut one shared ranked list — RULED 2026-10-26 (owner; this closes the question the rule
above left open).** The arms do not research equally by design: Baseline A does no research,
Baseline B researches at full depth (D2), and Proposed chooses a depth (D0, D1, D2) per request. The
planner runs once per request and emits **one ranked query list**. **D0 takes none, D1 takes a
prefix, D2 takes all.** Every arm that researches draws from the same ordered list, so the evidence
*available* is identical and only how much an arm consumes varies. The recording covers the full
list once; any depth replays a prefix of it. That keeps attribution clean: if Proposed at D1 beats
Baseline B at full depth, the difference is how it selected and used evidence, not that it saw
different evidence. Two conditions:

1. **The ranking is the shared planner's, not the Proposed arm's own logic.** If Proposed ranked
   the list and then took a prefix of its own ranking, the prefix would be privileged and the
   comparison contaminated. The ranking is computed before, and independently of, any arm's choice
   of depth, from the request alone.
2. **Which depth was chosen, and on what basis, is recorded per run.** Run state already carries
   `depth`, `research_score` (R) and `memory_coverage` (`Architecture.md` section 5.2); the record
   keeps all three and the threshold the depth was cut at. "Adaptive depth" is a claim that needs
   evidence the adaptivity actually varied, so the distribution of chosen depths over the 30
   requests is reported. If Proposed chooses D2 every time it contributes nothing, and the result
   reduces to "more search is better", which has to be known before the write-up, not after. The
   prefix length for D1 and the number of queries `q` are configured parameters, reported beside
   every result.

**The recording procedure (owner, 2026-10-26).** *A degraded search is never recorded into the
snapshot.* A search for which any engine was named as failed (`docs/searxng.md`) is not written; it
is retried later and the entry stays empty until a clean answer arrives. A frozen snapshot holding a
degraded search would make every arm replay the degradation permanently, and nobody reading the
results would see it. Development, tuning and calibration use the ordinary cache, which does hold a
degraded search but for its own, much shorter TTL (`research.search_cache_degraded_ttl_s`, one hour,
against a day for a clean one), marked degraded, so one bad minute cannot poison a day of runs.

**Degradation is measured against the declared engine set — RULED 2026-10-26 (owner).** "Degraded"
means an engine *in the declared set* failed, not that an engine SearXNG could have run did.
Without this the rule above could never be satisfied here: duckduckgo has returned a CAPTCHA on
every search since the first real one and wikipedia stays throttled (`docs/searxng.md`), so no
search would ever be complete. If those two stay down, the honest declared set may be brave alone,
and a brave-only search that brave answered is then **complete**, not degraded: complete with
respect to a configuration declared in advance and reported beside every result.

- **The set is NOT declared yet, and not by choice of the developer.** The rate probe decides it:
  the set is declared from the engines that actually answer, and recorded here, with the date and
  the evidence, before any recording. Until then `research.declared_engines` is `null`: undeclared,
  every engine SearXNG names counts as before, and the run report says `null`, visibly.
- **Where it is stated.** The declared set is in this section and in every run report
  (`SearchEvidenceReport.declared_engines`); a search carries it, a cached search is reused only
  under the set it was fetched under, and failures of engines outside the set are kept in the
  record (`undeclared_failures`) but do not make an answer degraded. The evaluation harness refuses
  to start a run while the set is `null` (when the harness exists, as it refuses a non-snapshot cache
  mode).
- **The cost, stated plainly.** *A single-engine configuration gives narrower coverage than a
  multi-engine one*: one engine's index and ranking, one engine's blind spots, and results that
  differ from what a multi-engine metasearch would find. *If that engine throttles mid-recording,
  the recording stops*: there is no second engine to carry on with, and the retry of a degraded
  search (above) waits for the one engine to recover. This is a real fragility of the evaluation's
  evidence, named here and to be named in the limitations of the write-up, not discovered during the
  recording.

### How long the recording phase takes — MEASUREMENT RUNNING, figure not yet available (2026-10-26)

*Degraded-search rate (added 2026-10-28; owner):* the time estimate below assumes a share of searches
degrade and are retried. The evidence for that share is a bound, not a measurement: zero failures of the
four declared engines in 45 searches puts the per-engine failure rate below about 6.5% (95%), and the share
of degraded searches anywhere from about none to about a quarter (see "The declared engine set"). The first
recording session logs it and reports it.

The snapshot ruling means the arms replay one recording, but the recording itself needs real
searches, and the live engines throttle this laptop's IP (`docs/searxng.md`): about ten searches in a
few minutes, then no recovery in twenty-five. How many searches per hour the three engines sustain is
unmeasured, so **the length of the recording phase is not yet known**, and no figure below is a
result.

*The first measurements were interrupted and are not rates.* A fixed one-search-per-10-minutes probe
ran twice: four searches (15:16 UTC, counts only, stopped by hand to add URL logging) and nine
searches (15:55 to 17:23 UTC, killed by the machine's memory-pressure reaper, below). Brave answered
9 of 9, duckduckgo CAPTCHA'd 9 of 9 and contributed nothing, wikipedia was throttled in 6 of 9 and
contributed nothing in any. A fixed 10-minute spacing could only ever measure itself (6 an hour is
its arithmetic maximum, and brave never approached a limit at it) and gave no recovery datapoint.
Both logs are kept as evidence of the attempt, with a README saying so
(`evaluation/search_rate/`).

*The measurement is therefore a ramp (owner, 2026-10-26).* `backend/scripts/searxng_ramp_probe.py`,
logic in `app/research/rate_ramp.py` (pure, 16 tests). The gap between searches steps down 5, 4, 3, 2,
1.5, 1, 0.75, 0.5 minutes with five distinct searches at each level; **stepping stops at the first
degradation of brave**, and the level it broke at is recorded. The highest *sustainable* rate is the
last level at which all five searches were clean, never the level that broke. It then backs off to
that level and keeps searching until brave answers cleanly again: the time from the break to the
first clean answer is the **recovery time**, believed only after a second clean answer in a row, and
capped at three hours. **Only brave steers it**: duckduckgo (9 of 9 CAPTCHAs) and wikipedia (zero
rows) are logged and never decide, and are excluded from the stepping logic on that evidence. If
brave never breaks, the report says the highest level *tested*, not a ceiling. Each search is one
fsynced line (so a killed run keeps what it did), with timestamps, the gap since the previous
search, per-engine status and reasons, result counts and every result URL. Its queries are distinct
within the run; the regulated-group list is a fixed list, so about five of its queries also appeared
in the 9-search run, over twelve hours before. `summarise_search_rate.py` reads the fixed-interval
logs; the ramp writes its own `summary` line. The result is the §8 input: the highest sustainable
rate for brave and the recovery time.

*Two confounds in the ramp, named before its result is quoted (2026-10-27).* (1) **Rate and count
are not separated.** A ramp that steps the gap down also accumulates searches: brave broke on the
7th search after 28 minutes, at the second search of the 4-minute level. That is consistent with a
rate limit (15 an hour is too fast) and equally with a budget (about six or seven searches in half
an hour, whatever the gaps). "The last fully clean level" therefore means "five searches at that gap
were clean", which is **not** a sustained rate: five searches over 20 minutes cannot show that 12 an
hour holds for hours. The fixed-interval run did show nine clean searches at 10-minute gaps over 1.5
hours. A **sustained hold** at the candidate rate for hours, after recovery, is what a figure to plan
the recording on needs; the ramp finds where to look, it does not certify a rate. (2) **Recovery is
measured while still searching.** After the break the ramp keeps searching at the back-off gap
until brave answers. If searching while throttled lengthens the throttle (the first throttling this
project saw, and a note in `docs/searxng.md`, are consistent with that and prove nothing), the
recovery time is over-measured. A cleaner variant would wait silently and probe once at increasing
delays. Neither is run yet; the result, when it lands, is reported with both caveats.

*Stop quoting 12 an hour (owner, 2026-10-27).* The planning candidate is **6 an hour**, not 12. Five
clean searches over twenty minutes is a burst. The fixed-interval run's nine clean searches at
10-minute gaps over 1.5 hours is the stronger evidence, and it sits at 6 an hour. Nothing in this
section is a planning figure until the sustained hold below has run.

*What brave did, 2026-10-27, as the ramp ran (UTC; answered = ✓, refused "too many requests" = ✗).*
05:36 ✓, 05:41 ✓, 05:46 ✓, 05:51 ✓, 05:56 ✓, 06:00 ✓ (the sixth in 24 minutes), **06:04 ✗** (the
seventh), 06:09 ✗, 06:14 ✗, **06:19 ✓** (26 rows, 15 minutes after the break), **06:24 ✗** (the
confirming search, refused again). So brave came back for exactly one search and then relapsed. The
ramp's controller did what it was built to do: it reset its confirmation instead of declaring
recovery, and it records the first clean answer at 900 s as a first clean answer, not as the
recovery time. No recovery time has been measured.

*Two readings, both hypotheses, both fitted to two runs.* Neither is a finding.

- **H1, a sliding window (the owner's reading): at most six requests in any trailing hour.** For
  it: the fixed-interval run never put more than six in any hour and all nine were clean; the ramp
  was refused on its seventh request within the hour. **Against it, within minutes of its being
  stated:** the answer at 06:19 came with ten requests in the trailing hour (seven answered, three
  refused), which H1 forbids, and the relapse at 06:24 is not what a window that had just let one
  through predicts. H1 as stated does not fit the 06:19 answer.
- **H2, a token bucket: a small capacity that each answered request spends one unit of, refilled
  slowly.** A search over a brute-force grid of capacities (1 to 12) and refill rates (0.3 to 24 an
  hour), with one unit per answered request and refused requests free, finds that a bucket of
  about **4.3 to 5.1** units refilling about **2.9 to 4.3 an hour** reproduces both runs exactly,
  the answer at 06:19 and the relapse at 06:24 included, and nothing else on the grid does (31 of
  about 44,000 pairs). Assumptions the fit makes and does not test: the bucket was full at the start
  of both runs; refused requests cost nothing; the cost of an answered request is the same however
  many results it returns. Three parameters (capacity, refill, starting level) fitted to two runs: it
  is a description of the data, not an explanation of brave.

*What H2 predicts, registered before the tests it concerns are run (2026-10-27, written by 06:27
UTC, before the ramp's 06:29 search; its log is the check).*
1. **A sustained 6 an hour will NOT hold.** At a refill of 2.9 to 4.3 an hour a 6-an-hour hold
   drains the bucket by about 2 to 3 units an hour, so a hold started from a full bucket is first
   refused at the **10th to 13th search** (2 to 2.2 hours in). If it instead stays clean for 24+
   searches, H2 is wrong and 6 an hour stands as measured.
2. **The sustainable rate would then be the refill, about 3 to 4 an hour,** not 6. The recording
   phase at 3 an hour is `S / 3` hours: q = 1, 10 hours; q = 2, 20 hours; q = 3, 30 hours. At 4 an
   hour: 7.5, 15 and 22.5 hours. That is two to three overnight sessions at q = 1 or 2, and four or
   more at q = 3. These are predictions of a hypothesis, to be replaced by the measurement.
3. **While the ramp keeps asking every five minutes, brave should answer about three to four times
   an hour with refusals between** (the saturated regime, in which successes arrive at the refill
   rate). This is observable now, in the ramp's own log, and costs nothing extra.
4. **The hold must start from a rested bucket,** or it measures the drained state and says nothing
   about the sustained rate: at least 90 minutes of silence before its first search (a full refill is
   about 1.2 to 1.6 hours at the fitted rates).

The decisive test is unchanged and does not depend on either hypothesis: hold at exactly 10-minute
gaps for at least 4 hours and 24 searches, distinct queries, from a rested start, and stop at the
first refusal. It either holds or it does not.

**Revision at 06:43 UTC, before the 06:44 ask: the H2 fit above did not survive 20 minutes of
data, and its predictions 1 and 2 are WITHDRAWN.** The four asks after the 06:19 answer (06:24,
06:29, 06:34, 06:39) were all refused. Every one of the 31 buckets that fitted both runs at 06:27
needed a refill of at least about 2.9 an hour, and at 2.9 or more the next token was due by about
06:40, so those fits predicted an answer at 06:34 or 06:39; there was none. Refitted to everything
through 06:39: **no constant token bucket fits both runs** (the first run, with a full start, needs
a refill of about 2.9 an hour or more; the second, through 06:39, allows at most 2.94 an hour, and
only with a capacity of 4.9 to 6.8). The second run alone still admits 52 buckets, all with a
refill under 2.94 an hour. So: either brave's limit is not one constant (it may differ between days,
by time, or by how many requests were refused rather than answered; refused ones were assumed free
and are not known to be), or the mechanism is not a bucket. No number is registered for the hold:
**it is a measurement, and the fitted 10th-to-13th-search prediction no longer exists.** What the
fits do still say is that the second run alone is hard to square with a sustained 6 an hour, and
the first run alone is hard to square with less, and the hold is what settles it.

*The extra hour is a test, registered here (owner, 2026-10-27: run the ramp until 07:30 UTC, then
stop it).* The ramp keeps asking every five minutes: ten more asks, **06:44, 06:49, 06:54, 06:59,
07:04, 07:09, 07:14, 07:19, 07:24 and 07:29** (answered = T, refused = X). Two descriptions make
opposite predictions about their pattern, and the log is the check:

| | Predicts for the ten asks |
|---|---|
| **A trickle** (a bucket-like limit; the 52 run-2-only fits) | No two answers in a row, ever. At most three answers (the fits give 0 in 6 of 52, 1 or 2 in most, 3 in 8), the earliest at 06:44, any two at least **20 minutes apart** (four asks), each followed by refusals until the next |
| **A window** (a dead period, then the limit clears) | A dead stretch of refusals followed by **two or more consecutive answers** (T T) |
| **Neither** | Anything else: for example answers that are not isolated and not consecutive-after-a-dead-period, or spacing under 20 minutes between isolated answers |

Zero answers through 07:29 is **not** a result for either: six of the 52 bucket fits predict it and
so would a window with a longer dead period; it is reported as undecided. The report gives the
sequence with the times and which row it fits, "neither" included. If two answers come in a row the
ramp's own rule (two clean in a row = recovered) ends it before 07:30 and that is itself the
window's prediction coming true. The 06:19 answer followed by four refusals is already in the data
and is not counted as part of this test.

*Result of the registered hour (written 2026-10-27, after the fact; the log is
`evaluation/search_rate/ramp-20261027T053618Z.jsonl`).* **The test did not complete, and its
verdict is "undecided".** The ramp's last ask was **07:09:20**. The machine was suspended from about
07:10 and woke at 08:29:37 UTC, which is when the 07:30 stop job, whose timer froze with it, finally
ran; so the ramp did not run past 07:30, but only **six of the ten registered asks were made**
(06:44, 06:49, 06:54, 06:59, 07:04, 07:09) and the other four (07:14 to 07:29) were never asked.
**All six were refused.** `classify_pattern("XXXXXX")` is `undecided`: no answer is the one outcome
a trickle and a window both allow. The log has no `summary` line (the process was stopped, not
finished). Neither description was confirmed or refuted by the test.

What the data does say, with no test attached: brave answered once (06:19) and then refused **ten
asks in a row** (06:24 to 07:09, one every five minutes), 50 minutes after that answer and 65
minutes after the break. Refitted to everything through 07:09: **no constant token bucket fits both
runs (0 of about 44,000)**, and the second run alone now admits only 10 buckets, all with a refill
of at most 1.14 an hour and a capacity of 6.2 to 6.8; the first run's nine clean answers at 10-minute
gaps need more than that allows. The bucket description is dead for brave as a single constant.

*Registered 2026-10-27, committed at 16:56:31 UTC, before the silent-wait's 10-minute probe
(17:00:02); its 5-minute probe at 16:55:03 is known (refused) and is not part of this.* The silent-wait is measuring recovery
from the sustained hold's break (16:50:02) with **nobody asking** in between, which is the one
comparison the ramp could not make. Two readings, with different predictions, neither fitted:
(A) *asking was the cost*: the ramp's refusals lengthened its own throttle, so in silence the first
clean probe comes **well inside the ramp's 50 minutes of failed asking (by the 45-minute probe)**
and the confirming probe ten minutes later is **also clean**; (B) *the limit refills slowly whether
or not anyone asks*: the first clean probe is **no earlier than the ramp's 15 minutes** and the
confirming probe is **refused again** (a relapse, as at 06:24). Anything else, including no clean
probe by 180 minutes, is "neither". The result is an interval between two probes, never a point.

*Result (log `evaluation/search_rate/silent-20261027T165440Z.jsonl`; one run).* Brave refused every
probe at 5, 10, 15, 20, 30, 45 and 60 minutes after the break, **answered the 90-minute probe
(18:20:03, 90.0 minutes after 16:50:02), and refused the confirming probe ten minutes later
(18:30:04).** Recovery lies between 60 and 90 minutes after the break; the probes at 120 and 180
minutes were not needed and not made. Against the registered readings: (A) is **not** met (no clean
probe by 45 minutes, and the confirming probe was refused); (B) is met as written (first clean no
earlier than 15 minutes, confirming probe refused). What this supports and what it does not: in
silence this throttle lasted 60 to 90 minutes, longer than the ramp's 15 minutes *while being asked*,
so asking was not shown to lengthen it; and a single clean answer was followed by a refusal, as at
06:19 in the ramp. It does not give a refill rate, does not distinguish a slow bucket from a ban of
fixed length, and rests on one break. The earlier silent launch (`silent-20261027T165329Z`) made no
searches and is kept as a record of the rest bug.

*A third reading, untested and not fitted: asking while throttled lengthens the throttle.* Ten
straight refusals while asking every five minutes, against an earlier throttling this project saw
last for over twenty-five minutes while being asked, is what that reading expects; so would a
ban of unknown fixed length. The data cannot tell them apart. The silent-wait recovery test is built
to: provoke a throttle, stop asking, and probe once at growing delays. If recovery then comes in
minutes, asking was the cost; if it takes as long, it was not. It has not been run.

*Consequence for the hold.* Seven and a half hours of silence followed the last ask (07:09:20 to
the time of writing, about 14:40), so the rest the hold requires is satisfied many times over and
whatever caused the refusals has had that long to lapse. The hold's first search is therefore also
an unplanned datum: whether brave answers after about 7.5 hours of silence.

### The sustained hold: 6 an hour did not hold (2026-10-27)

*Result (log `evaluation/search_rate/hold-20261027T152950Z.jsonl`).* Ten-minute gaps from 15:29:52 UTC,
on AC power. **Brave answered eight searches (15:29:52 to 16:40:01) and refused the ninth at
16:50:02 ("too many requests"), 1.336 hours in.** Every gap was 600.9 to 601.7 s; there was no late
ask. The hold ended at the first refusal, as designed, so **6 an hour is not a measured sustained
rate**: it lasted about 1.3 hours, not the 4 hours and 24 searches that would have certified it. This
settles the planning question in one direction only: the sustainable rate is **below 6 an hour**, by
an amount not yet measured. It does not say what the rate is.

*What it was run against, stated.* The machine had been silent for 7.7 hours; two earlier searches
(14:49:07 and 15:17:33, from the stopped attempt) preceded it by 12 minutes, so brave had seen 10
answered requests in the 2 hours 1 minute before the refusal. The refused request was the sixth in
its trailing hour, and the 16:19:58, 16:29:59 and 16:40:01 requests had each been the sixth in
theirs and were answered: **a limit of six per trailing hour (H1) is refuted** a second time, now
by a refusal at exactly six. The three runs do not agree on a constant: fitted to a token bucket
(capacity 1 to 12 units, refill 0.12 to 24 an hour, full at each run's start) runs 1 and 3
(the 9 answered at 10-minute gaps, and this hold) fit **388** buckets together, refilling at most
4.5 an hour; **the ramp fits none of them (0 for runs 1+2, 2+3 and 1+2+3)**. A variant in which
refused requests cost units fits no better. The one thing the ramp did that the other two did not is
ask repeatedly while being refused, which is what the silent-wait compares (above).

*The sleep check you asked to be logged, answered.* The Windows log shows **five Modern Standby entries
during the hold, while on AC power** (15:52:06 to 15:52:31, 16:00:57 to 16:09:38, 16:22:19 to
16:25:31, 16:30:34 to 16:31:31, 16:38:50 to 16:38:55). **None stopped the hold**: no late ask, gaps
within 1.7 s of 600. The same was true of the two standbys on AC before the setting changed (05:19
to 05:32 and 05:40 to 06:03, through which the ramp kept asking every 300 s). So on this machine a
Modern Standby on AC power does not freeze a job, before or after the change: **what froze work
was running on battery**. The AC sleep timer set to "never" is not shown to have mattered; being
plugged in is. The keep-awake request is likewise unshown. (`Architecture.md` section 11.)

*For the recorder.* A recorder paced at 6 an hour for hours would have been refused, as the hold
was; one paced at the average the hold actually achieved before refusal (10 answered in 2.0 hours,
about 5 an hour) lasted that long and no longer. Both are why the recorder is adaptive
(above): its pacing starts below the best rate seen and moves with the refusals. The session budget
is a range: at 4 to 5 an hour, 30 × q searches take 6 to 7.5 hours for q = 1, 12 to 15 for q = 2 and
18 to 22.5 for q = 3, plus the waiting after each refusal, which is why it is a range and not a
schedule.

*The arithmetic that turns a rate into a duration.* Searches to record: `S = 30 × q` (the planner
runs once per request; above), `q` configured and not yet chosen, three used for illustration. At a
sustained rate of `R` searches per hour the recording phase takes `S / R` hours of wall clock, and
the laptop must stay on for all of it. For illustration only, `q = 3`: 90 searches; at `R = 6` (one
per 10 minutes, the probe's own spacing) that is 15 hours, just over one 14-hour overnight session;
at `R = 3`, 30 hours. These come from the arithmetic, not a measurement; the measured `R`
replaces them. A degraded search being retried (above) lowers the effective `R`.

*The decision rule (owner, 2026-10-26).* If the measured recording phase is more than a few overnight
sessions, **the design changes** (a different search source, fewer queries per request, or a smaller
recording) rather than being discovered in November. The figure goes here when the probe has run.

*PDFs (owner, 2026-10-26).* PDFs are not supported (`docs/fetcher.md`); a PDF is skipped visibly as
an unsupported content type. Whether that matters is measured, not assumed, **and measured cheaply**
because the search engines already throttle this IP: (1) by extension first, which needs no request
(a path ending `.pdf`); (2) for the rest, **HEAD, never GET**, on a seeded random sample, not on every
URL, with robots.txt honoured and no fallback to GET when a server refuses HEAD (counted unknown);
(3) the share is reported as an estimate with its 95% interval, the by-extension count plus the
sampled rate over the remainder, with unknown samples reported and left out of the rate
(`scripts/classify_result_types.py`, 11 tests; `--dry` shows the number of requests before any is
made). A couple of percent stays a footnote; a fifth of the regulated sector's results is a real
gap and the owner rules again. The by-extension count alone, from the 9-search log and with no
request made, is a **floor**: 10 of 120 distinct regulated-group URLs (8.3%) and 1 of 96 general ones
(1.0%). It is a lower bound from nine searches and not the answer.

**Quantified limitation (2026-10-28).** PDFs are skipped; they were 15% of URLs in a measured search (12 of 79, 2026-10-28), and the regulated-sector share by extension alone was 8.3%. The 15% is one search, a regulated-sector query
(12 of 79 distinct URLs, from `scripts/fetch_cost_probe.py`, `evaluation/fetch_cost/fetch-20261028T033217Z.jsonl`);
the 8.3% is the by-extension floor from nine searches. Both are lower bounds on the true PDF share
(a PDF served from a path without `.pdf` is not counted), neither is an estimate with an interval, and the
sampled HEAD estimate below has not been run. Every evaluation report states this beside the evidence it
uses: evidence that exists only in a PDF is absent from E, not marked weak. **PDF support is deferred, not
built** (owner, 2026-10-28): Phase 6 and an end-to-end system come first, and the deferral now rests on a
measured number.

**Ruled 2026-10-27 (owner): 50 HEAD requests, all on the regulated group, none on the general one.**
The general group's free floor (1 of 96 by extension alone) already answers it and needs no request;
the open question is only whether regulated PDFs are a real share, and 8.3% from nine searches
already suggests they are, so the requests are spent where the answer is in doubt
(`--sample 50 --sample-group regulated`; the general group is reported as a floor, never as an
estimate, since nothing in it is sampled). **Not run yet:** it waits for the ramp to finish and a
fuller URL log, as instructed.

### The recording is captured request by request, atomically, and is resumable (owner, 2026-10-27)

> **AMENDED 2026-10-28 (owner): the atomic rule is RELAXED to session-scoped reuse.** Disclosed here, in
> the same terms as the hold's stop rule (changed after seeing its cost): the rule below was relaxed after the
> session loop showed what it costs, and *no live recording data existed when it changed*; the cost was seen in
> the loop's design and in its tests with simulated refusals (a refused request threw away every clean search
> inside it and redid them all, and with a throttle that bites late in a request that repeats). **What the rule
> was for:** a request's queries are captured close together, so its snapshot is internally coherent. **What
> now holds:** a clean search captured for a request may be reused by a later attempt at the SAME request in
> the SAME session. Within one session coherence survives reuse: the worst case is the session's wall limit
> (10 hours) against a per-request arm window of 3 days, so discarding clean searches bought a guarantee
> tighter than the one the design already accepts, at the price of searches. **Across sessions nothing is
> reused:** a request not committed when a session ends starts clean (the carried searches live in memory for
> the session only and are never read from the store). **The property is now reported, not guaranteed by
> construction:** every request's committed line records its internal capture span (first to last search,
> `search_span_s`, and `search_span_all_s` which also counts answers reused from OTHER requests at the time
> they were first fetched), and the recording reports the maximum and median span across the 30 requests. If
> spans are minutes nothing was lost; if one is six hours it is visible and the analysis can say so. The
> 3-day arm window runs from the request's EARLIEST search. `searches_wasted` stays in the report and now
> measures only the cross-session loss (clean searches held for a request still uncommitted when the
> session ended), the number that says whether sessions are ending badly; `searches_carried` counts the reuse.
> Pages are unaffected (a request's pages are fetched after all its searches, in one pass).

**The rule: a request is captured atomically or not at all.** If a session dies partway through a
request's ranked query list, that request's partial capture is **discarded** and the request is
redone from its first query in the next session. Resumability works at **request** granularity, not
search granularity, and the manifest tracks completed *requests*, not completed searches. *Why:*
each request's snapshot is internally coherent, captured within one session, and the 3-day window
(above) runs cleanly from that one capture time. A straddled request would have query 1 from Monday
and query 3 from Wednesday, and the analysis would then be reasoning about staleness *within* a
request as well as between arms. *Cost:* a handful of redone searches, at most the clean ones
already done for the request in flight. This closes the question left open earlier of how a request
that straddles sessions is treated against the window: **there is no such request** (amended 2026-10-28 above:
no request straddles SESSIONS; within a session its span is reported).

*Why resumability at all.* The machine that runs the recording can be killed at any time, and on 2026-10-26 it was: the
memory-pressure reaper stopped an overnight job while the session was idle (`Architecture.md` §11,
"Worker-host memory"). The recording is the largest overnight job in the project; a kill at search
180 of 270 with nothing durable loses the night, and with the search rate this low a night is not
cheap. Sleep, a reboot and Docker restarting are the same event. So **the recording procedure must
survive its own death**, and this is a requirement of the procedure, stated before the snapshot mode
is built so that it is built in rather than added after:

1. **Staged, then committed in one step.** A request's searches are written to a staging area as
   they complete, for diagnosis only: a restart never resumes from staging. When the last search of
   the request has come back clean, the whole request is committed to the write-once snapshot in a
   single atomic operation (one Redis transaction), and only then is its manifest line written. A
   staged request that was never committed is **deleted, not repaired**, and never touched the
   write-once snapshot; this is how "discard" is compatible with "never overwritten".
2. **A manifest of completed requests.** One append-only line per *committed request*, flushed and
   fsynced: the request, its ranked query list and normalised keys, the declared engine set, the
   time of its first fetch and of its commit, each query's result count, a content hash of the
   committed set, and the number of attempts it took (discarded partials are logged as attempts in
   the same file, never as captures). A request has no line until it is committed. The manifest is
   the answer to "what is already done" and the record that the recording was complete.
3. **A restart verifies, discards, and continues at the next request.** On start the recorder reads
   the manifest, checks that each committed request's entries exist and match their hash, deletes
   any staging left over, and reports "N of 30 requests captured, M to go, K partials discarded"
   before it does anything. A request in flight when the process died has no manifest line and is
   **redone from its first query, never continued and never assumed**.
4. **The line and the commit must agree.** The manifest line is written after the commit has been
   read back and its hash checked, so a line means the request's entries exist. A committed request
   with no line (killed between the two) is verified against its hash and given its line, not
   fetched again; a line whose entries are missing or differ (the Redis AOF can lose up to a second
   after a crash) marks the request outstanding, and it is redone.
5. **A degraded search is an attempt, not a capture.** Inside a request it is retried within the
   session (above). If the session ends first, the request is discarded like any other partial. A
   committed request therefore never contains a degraded search.
5a. **The window runs from one capture time.** The N = 3 days window of the earlier ruling is
   measured from the request's first fetch in the session that committed it; every query of the
   request was fetched in that session.
6. **The recording stops cleanly when the engine does.** ~~A throttle that persists past the
   recovery time measured by the ramp ends the session~~ **(superseded 2026-10-27 by the adaptive
   recorder below: no measured recovery time exists, and a fixed one would be a schedule again.
   The stop rule is the dark-period ceiling.)** When a session ends it ends with the manifest
   intact, the in-flight request discarded, and a stated resumption point, not a crash. Requests
   are recorded in the test set's order, one at a time.
7. **The manifest is committed with the snapshot's hash and date range** (the snapshot itself is
   not, being third-party content), so "the recording covered these requests under this declared
   set" is checkable from the repository.
8. **The recording does not load the encoder** (owner, 2026-10-27). Recording is search, fetch and
   store; the encoder is the largest consumer on the worker host (1.03 GB resident after embedding,
   `Architecture.md` §11) and the recording is the long overnight job that a memory shortage already
   killed once. Nothing on the recording path imports `sentence_transformers`, `torch` or
   `transformers` at import time, and a test in CI's Backend job makes that impossible rather than
   remembered (`tests/test_recording_does_not_load_the_encoder.py`: a static import-closure check
   that runs in CI, which cannot install the encoder, and a runtime check where it is installed;
   both shown to fail on a direct import, a transitive one, and a recorder written outside the
   listed modules). The browsers are closed during recording and evaluation runs: 2.4 GB for
   something nobody is watching at 3 a.m.

### The recorder is adaptive, not scheduled (owner, 2026-10-27)

*Why.* Brave's limit may not be one constant. The two runs so far cannot be reconciled by any
constant bucket, and the limit may vary by day, by time, or by whether refused requests count
(the ramp's confounds, above). If it varies, no measurement yields a stable planning figure, and a
recording scheduled at a fixed rate either wastes hours (the limit was higher) or walks into a
throttle (it was lower). So the number the hold produces is **a starting estimate, not a
parameter**, and the recorder must not depend on it being right. Design (not built; the recorder
does not exist yet, and nothing here is tuned):

1. **Ask, back off on refusal, retry with increasing delay, resume when clean.** After each answered
   search the recorder waits the pacing gap and asks the next. A refusal does not end the request:
   the same query is retried after a delay that grows on every consecutive refusal (from a base to a
   ceiling, with jitter, so retries never form a tight loop) and the pacing resumes the moment an
   answer comes. A degraded answer is a refusal here (above: it is never recorded).
2. **It targets the measured rate and does not trust it.** The pacing gap starts at the hold's
   estimate. Each refusal lowers the target rate (multiplicative decrease); a run of clean answers
   raises it again (additive increase) up to a cap relative to the estimate. This is ordinary
   congestion control, chosen because it needs no model of why the limit moves, only the signal
   that it did. Its parameters (the base and ceiling of the retry delay, the decrease factor, the
   increase step and run length, the cap) are configuration, **untuned defaults**, reported beside
   every result like every other parameter.
3. **It records its own achieved rate as it goes.** Every attempt logs its time, outcome, the delay
   it waited, the requests in the trailing hour, the target rate and the achieved rate (clean answers
   per hour, over the trailing hour and since the session began), so a session's report states its
   achieved rate, its refusals and the time it spent waiting, and the next session starts from an
   observed rate, not from the hold's. The per-request manifest line records the attempts and the
   waiting that request took.
4. **A night that goes badly costs searches, not the session.** With per-request atomicity and the
   manifest, a throttle costs the waiting plus at most the clean searches of the request in flight,
   and a killed process costs the same. Nothing already committed is at risk.
5. **A session ends on a dark-period ceiling, not on a measured recovery time.** If no search has
   been answered for longer than the ceiling (configuration), or the wall-clock limit is reached,
   the session ends cleanly (item 6 above): the in-flight request is discarded, the manifest says
   where to resume. A dead night ends instead of burning its attempts into a ban.
6. **Whether a retry is harmless is still open, and the delays are configuration so either answer
   fits.** The silent-wait test has run once (above): recovery took 60 to 90 minutes with nobody
   asking, and the first ramp recovered after 15 minutes while still asking, so asking was not shown
   to be the cost. What that does show is that retry delays of minutes are futile against a throttle
   of this length: they spend requests and recover nothing. The default stays retries with increasing
   delay, **provisional**, and the delays are configuration; whether to pause for about an hour
   instead is the owner's decision, on one run.

*What it changes about the hold.* The hold's job shrinks to what a planning figure needs: **roughly
how many overnight sessions to budget.** The budget is a range, `S / (achieved rate × session
length)`, with the rate the hold measured as its centre and the spread of the ramp, the hold and the
first sessions as its width; it is revised after each session from that session's achieved rate. It
is not a promise that a session finishes a fixed number of requests.

*What adaptivity does not change.* A request is captured atomically or not at all; a degraded search
is never recorded; requests are recorded in the test set's order; the recording is resumable; it
does not load the encoder; the declared engine set is fixed in advance and reported. The pacing may
vary between sessions; the recorded evidence may not.

### The seven-engine hold (owner order 2026-10-28; registered before it starts)

*Registered 2026-10-28, committed before the first search of the hold; the burst and the single
search that preceded it are known and are not part of the test.* **Steering set (a candidate, not a
declaration; `research.declared_engines` stays null):** yep, yandex, yahoo, privacywall,
duckduckgo web, swisscows, mojeek: the seven that answered 8 of 8 in the 2-minute burst
(`evaluation/search_rate/engines-burst-20261028T025520Z.jsonl`). **Brave stays enabled and is
ignored by the stepping logic**; so do the other fourteen. **Protocol:** fixed 2-minute gaps, no
rest first (the seven were last asked at 03:10 UTC and have been asked at 2-minute gaps for 16 minutes
at most: this is NOT a rested start, and the log says how long the silence was), 120 searches AND
4 hours, stop at the first search in which ANY of the seven is named as failed, report which.
`scripts/searxng_hold_probe.py --engine yep,yandex,...`, logging every result URL.

*Readings, fixed now.* (1) **Held** (120 searches and 4 hours, none of the seven failed): the seven
sustain 30/hour for 4 hours; recording 30 x q searches takes hours, not nights, for any q the planner
asks for up to about 120 per session. One run, one IP, one day: it says nothing about tomorrow's
budget. (2) **Broke before the 30th search**: a count-like limit on the engine named; slowing buys
little. (3) **Broke between the 30th and the 120th**: a rolling or hourly budget the 16-minute burst
could not reach; the trailing-hour count at the break is the figure. (4) **Broke on an engine for a
reason other than 'too many requests'** (CAPTCHA, access denied, timeout): that engine leaves the
candidate set; this is a statement about that engine's tolerance of an automated client, and the
hold's other six are still read up to that point. A zero-row answer from a steering engine without an
error is logged and does NOT stop the hold (a legitimate query can return nothing); it is reported.
A late gap (machine slept) is recorded and a hold with one is `held_with_gaps`, never `held`.

*Result (2026-10-28, `evaluation/search_rate/hold-20261028T062237Z.jsonl`; the laptop was on AC).* **The
hold broke at the SECOND search, 125 s in, on yep: HTTP 503 from `api.yep.com`** (SearXNG's own error
stats: 1 of 10 yep requests failed, 10%; yep had answered 20 rows in each of the 8 burst searches and in
the hold's first). That is reading (4), not reading (2) or (3): not "too many requests", so it is not
evidence of a count or a budget, and it says nothing about whether the other six hold for hours: they were
observed for two searches, clean. Two things worth keeping from it. (a) **The stop-at-first-failure rule
makes a hold of any set as fragile as its flakiest member**: one transient 503 ends a four-hour run in two
minutes, and it cannot tell a throttle from an outage. (b) **mojeek answered ZERO rows in that same search
with no error named** (10 rows in the first), a silent empty that the rule, as registered, logs and does not
count. A searcher that returns nothing without saying so is the failure of the throttled empty 200 again
(decision log 7.2), at the engine level; SearXNG's error stats list no mojeek errors, so the cause is
unknown. Nothing here declares or undeclares anything. Next measurement: the owner's.

#### The seven-engine hold, second run: run to completion (owner order 2026-10-28; registered before it starts)

*The stop rule was changed AFTER seeing the first attempt's result* (it ended at search 2 on a yep 503 and
a silent mojeek empty): this is stated, not hidden. The first attempt's two searches are known and are not
part of this test. **Same seven engines (yep kept), same 2-minute gaps, 120 searches AND 4 hours, laptop on
AC.** Brave stays enabled and ignored. **New rule** (`SetHoldController`, 9 tests, `scripts/searxng_hold_probe.py
--engine a,b,...`): the run goes to completion; every engine's status is recorded on every search as
`ok` (rows), `empty` (no rows AND no error named, logged distinctly) or `error` (named as failed); a single
failure stops nothing; an engine failing **3 searches in a row** (empty counts) has FALLEN, leaves
steering, and its search number and reason are recorded; the run stops early only if **fewer than 4 of the 7
answer with rows in one search** (a fallen engine that answers again still counts for this). Outcome:
`held` (none fell), `held_with_falls`, `held_with_gaps` (the machine slept; never `held`), or `collapsed`.

*Readings, fixed now.* (A) **All seven sustain**: no engine falls in 120 searches; recording takes hours,
not nights, for any q the planner needs up to about 120 searches a session. (B) **Some fall**: the
engines, the search numbers and the reasons are the result; the engines that did not fall are the
sustainable set at 2-minute gaps for the hours observed, and a fall at search k with k small is a
different finding (a count) from one at k large (a budget). (C) **Collapse**: fewer than 4 answering in a
search ends the run; the search number and the engines still answering are reported. (D) **Clustering,
mechanical:** falls and failures are *clustered in time* if at least half of all engine-failures occur in
searches in which two or more engines failed; *clustered by engine* if at least half belong to one engine;
both can hold; neither is *scattered*. Correlated failure (a shared upstream or this IP's reputation) is
read from the time clustering, and it is reported per engine pair (searches both failed). **Reading
caveat fixed in advance:** SearXNG suspends a failed engine (180 s for access denied / too many requests,
3600 s for a CAPTCHA, 5 to 120 s growing for other errors), so at 2-minute gaps a failure may be followed
by a `Suspended: ...` search in which the engine was not asked: three failures in a row can be one fresh
refusal and its suspension. The analysis separates fresh failures from `Suspended` ones and says which a
fall consisted of.

### The query planner (design approved 2026-10-28; `backend/app/research/planner.py`)

**One ranked list, from the request alone.** The planner runs once per request, before any arm picks a
depth, and emits one list; D0 takes none, D1 the first pass, D2 all of it (the rulings above). **Facets**,
each with a named consumer (a facet without a consumer does not exist): (1) competitor and category
positioning, A2; (2) audience needs and objections, A3; (3) independent reference facts for the product's
attributes, A1 and A7; (4) sector rules and advertising norms, sector risk and A7. A fifth, a timeliness
hook, was considered and dropped: it needs keyword extraction from free text, and recency is already scored
per source. **Ranking is a breadth-first interleave**: pass 1 is every facet's primary query in the facet
priority order, pass 2 every facet's second-angle query. So D1 (the first pass, 4 queries) covers every facet
once, a shorter prefix provably leaves a facet uncovered, and a cut forced by the budget removes the tail
(the lowest-priority facet's second query) before any facet. **The facet priority is untuned developer
judgement** (a regulated sector leads with sector rules, otherwise competitor positioning leads) and is
reported with every result. `q = 4 facets x 2 = 8`; the per-facet count is `research.planner_queries_per_facet`.

**QUERY GENERATION IS NOT ADAPTIVE.** The queries are filled from fixed templates by the request's fields
(sector, regulated flag, category, audience) and by nothing else: not memory, not earlier results, not the
arm. This is a thesis about adaptive marketing intelligence, and the adaptivity it studies lives in depth
selection and memory reuse. The query generator is a lookup, and the thesis says so. Templates were chosen
for being reproducible, free, testable offline and fixed when the request is recorded; the cost is generic
phrasing (an LLM paraphrase behind the same interface is a Phase 6 option, not evaluated). The objective is
carried on the plan and not used in query text, because using it would need the keyword extraction declined
above.

**q = 8 is CONDITIONAL on a measurement, and not yet confirmed (owner, 2026-10-28).** A second query only
adds breadth if it retrieves different things. With about 77 unique URLs per search the second angle on a
facet may overlap the first so heavily that D1 and D2 differ in redundancy, not coverage, and "depth"
measures volume. **To be measured before q is fixed:** for a handful of real requests, the URL overlap
between query 1 and query 2 within each facet (`scripts/planner_overlap_probe.py`). Low overlap: q = 8
stands. High overlap: the templates diverge more, or q drops to 4. The figure is reported here. **Registered before the measurement (2026-10-28): the metric and the thresholds.** Overlap is the
containment of the smaller result set in the larger, on canonical URLs, within each facet between its
query 1 and query 2, over three requests (R01 consumer electronics, R19 health supplements, regulated, R25
home furnishing; 24 searches, the candidate seven-engine set, 2-minute gaps, no search counted if a steering
engine failed). **Median within-facet containment below 0.30: q = 8 stands. 0.50 or above: the second
query is not earning its place (templates diverge more, or q drops to 4). Between: the owner decides.**

**Middle band RULED before the number exists (owner, 2026-10-28): between 0.30 and 0.50, q STAYS AT 8 and the
measured overlap is reported beside every result.** The reason is structural, not a preference. Dropping to one
query per facet makes D1 and D2 identical and destroys depth as a variable, which is the thing the second
query exists to create. So in the middle band the alternatives are to keep 8 with the partial redundancy
reported, or to restructure the facets, and restructuring on a single overlap measurement (three requests,
one day, one engine set) would be overfitting the design to one probe. **At 0.50 or above** the second query
is not earning its place and the owner rules on restructuring the facets, not on q alone.

**RESULT (2026-10-28; `evaluation/planner_overlap/overlap-20261028T083221Z.jsonl`; 24 searches, R01, R19 and
R25 from the frozen category field, the seven candidate engines, 2-minute gaps). q = 8 STANDS, as
registered; no restructuring (owner).**

*Lead result: the four facets retrieve genuinely different things.* The URL containment between the first
queries of DIFFERENT facets is **0.01** (n = 18 pairs); between a facet's own two queries it is **0.10**
(n = 11). A tenfold separation. The facet design was judgement, the developer's and the owner's; this is the
first evidence it corresponds to anything, in the narrow sense that different facets bring back different
pages. It does not show that the pages answer the facet's information need (relevance is not measured), and
it is 3 requests on one day.

*Within-facet, against the registered bands.* Median containment between a facet's query 1 and query 2:
**0.10** (range 0.00 to 0.36), below the 0.30 band edge: the second query earns its place and depth
(D1 against D2) differs in coverage, not only in volume. By facet (n of 2 to 3 each, **pointers, not
estimates**): audience_needs 0.00, reference_facts 0.08, sector_rules 0.10, competitor_positioning 0.34.
1 of the 24 searches was excluded (R01 competitor_positioning, query 2: a declared engine failed, so the
search was degraded; the probe's record does not say which engine), leaving 11 of the 12 possible pairs and
competitor_positioning with 2.

*competitor_positioning is the one facet whose two angles are most alike (0.36 and 0.32, n = 2). Recorded,
NOT acted on:* two pairs are no grounds for changing a facet design, and acting on them would be the
overfitting the registered thresholds exist to prevent. It is re-examined if the recording's own data (153
searches) shows the same pattern at scale.

*Hosts overlap more than URLs: 0.22 against 0.10.* The same sites supply different pages, so the evidence
base is narrower by host than by URL. That bounds how independent the sources are, which is V6's territory
(authority against independence). **Hosts per search and host overlap (within a facet and between facets)
are tracked during the recording and reported beside the URL figures**, not URLs alone.

*Caveats, as measured:* 3 requests, 11 pairs, one day, one engine set (the seven, not the declared four), 23
clean searches of 24. The measure is containment on canonical URLs.
Those thresholds are developer judgement, untuned, and chosen before any number exists. The cross-facet
containment of query 1 against query 1 is reported as the baseline for reading the figure (two queries about
different needs that overlap this much are already as redundant as unrelated ones can be), and the by-facet
medians are reported because one facet's second query may be redundant when the others are not. Until then
8 is the design value and nothing is recorded under it. The measurement waits for the seven-engine hold to
finish: searches issued during it would be load the hold is not measuring.

**Multi-hop: considered, found COMPATIBLE with search held constant, dropped on scope.** The architecture
document describes D2 as "multi-hop". That is not incompatible with the rule that search is held constant:
the planner runs once per request, a hop-2 query derives from hop-1's recorded results, and the whole tree is
recorded once and replayed by every arm, so arms would draw prefixes of a tree instead of a list. It is
dropped because hop-2 derivation needs its own logic (what makes a good follow-up query) and that logic
would need its own justification without being this thesis's contribution. D2 therefore means all 8 queries
of the one list. (An earlier draft of this record called it incompatible; that was wrong.)

**The category field (added 2026-10-28; owner).** The planner's one product term is a generic category, and
the test set had none: a missing field, found by needing it. `evaluation/test_set.yaml` now carries a
`category` per product (20 products, 30 requests), authored by the developer from what each request describes
and, like every other untuned input, reported as such; no brand or product name is in one (a test checks).
It is frozen by `evaluation/test_set.categories.lock` (a SHA-256 of the 20 categories; a test fails if one
changes, so a change is deliberate and dated). The rest of the test set is not locked. R19, the regulated
request, is a whey isolate + concentrate blend (animal-derived, "whey protein powder"), not a plant protein;
the file was read before the category was fixed. The overlap probe takes R01, R19 and R25's categories from
this field.

**How much research variety 30 requests give the planner (measured 2026-10-28, `scripts/planner_variety.py`;
no network).** Requests that share a category share every query that does not read the audience: 7 of the 8.
- Distinct ordered query lists: **30 of 30**, but only because the audience facet's primary query reads the
  audience hint. Distinct **query texts: 153 of 240 slots**. Distinct first-pass (D1) texts: 77 of 120.
- Distinct categories: **19**. Distinct audience-free cores (the 7 shared queries): **19**. **11 requests
  repeat an earlier request's core**: R01/R03, R04/R06, R07/R09, R10/R12, R13/R15, R16/R17/R18, R19/R21,
  R22/R24, R25/R27, R28/R30 (campaign 3 reuses campaign 1's product; B06's two products share one category).
- **RULED 2026-10-28 (owner): accept it, and report it as DESIGN, not limitation.** Adding the objective to
  the query text would make campaign 3 see DIFFERENT evidence from campaign 1 on the same product, which
  confounds exactly the comparison the test set exists for: the Proposed arm's advantage on a repeat campaign
  would be "it remembered" mixed with "it saw new evidence". Diversifying the categories does the same thing
  more weakly. So repeat campaigns on the same product keep **identical research evidence**, because that is
  what isolates memory reuse from fresh evidence. Reported accurately: **30 requests over 19 distinct
  category situations, 11 requests repeating an earlier core**; "30 requests, 30 research situations" would be
  false. **Recording budget: 153 distinct searches, not 240 (for ONE declared engine set, and the declared set is
still undecided)**, and the collapse is verified, not assumed:
  `cache_key` is the SHA-256 of the engine-set scope and the normalised query, and a test
  (`tests/test_evaluation_test_set.py`) computes the key for all 240 planned queries, and for case- and
  space-mangled copies of them, and finds 153 distinct keys. The engine set is part of the key, so the 153
  hold only under one declared set: if the declaration changes, so does every key.

**Sub-analysis, registered before any data (owner, 2026-10-28): the evidence stage, THREE ways.** Every request
is tagged from the test set alone (`evidence_tags` in `backend/app/evaluation/test_set.py`, 6 tests) with one
of three stages: **cold start** (position 1; 10 requests; no memory by construction), **warm novel** (position
2 or 3 on a category not seen before in the set's order; 9) and **repeat** (a category an earlier request in the
set already used; 11, of which 10 are the same product and 1, R17, a different product). **The three-way split
is the primary reporting.** The two-group version (novel against repeat) is derivable from it by pooling the
first two stages and is not carried as a separate claim: a "repeat beats novel" taken alone could be nothing
more than "warm beats cold". Every primary metric is reported per arm for each stage, with the number of
requests in each cell. The **margin** is Proposed's advantage over Baseline B (the arm that researches at full
depth with no memory), signed so that positive is better for Proposed.

*The prediction, stated before any run:* a memory mechanism that works shows **repeat margin > warm-novel margin
>= cold-start margin**, the cold-start margin near zero (Proposed has no memory to use there).

*What the prediction can and cannot show, written down first because the cells are 10, 9 and 11.* **It is
asymmetric.** Observing the predicted ordering is WEAK evidence: three noisy means can order themselves
correctly by chance, and it will be reported as **consistent with** a working mechanism, never as support for
one. Observing it **reversed** is much STRONGER evidence: a memory mechanism that works should not produce a
larger margin on cold starts than on repeats, so a reversal is reported as **evidence against** the
mechanism (or against the measurement) and not as noise. A confirmation reads in a thesis as more than it can
carry unless this is on record before the data, which is why it is here.

*Per-request margins are reported, not only cell means.* All 30 per-request margins are shown for each primary
metric, each labelled with its request, stage and (for repeats) subtype, so that a reader can see whether an
ordering rests on one outlier or on a consistent pattern. Three means hide exactly that at n = 10. This
replaces a leave-one-out run for each suspicious request: R17, the one `different_product` repeat, is simply
marked in the display, with every other request. Cell means carry intervals and no significance claim on their
own.

**B06 (R16, R17, R18): three requests on two products sharing one category.** Read from the test set:
product B is the hazelnut flavour of product A (same pack, same protein, "Hazelnut flavour" the only added
verified claim), and the design asks only that campaign 2 be a "same brand, related product"
(`Memory.md` section 7: "3rd+, same product family"; this plan's test-set table: "same or adjacent
product"). So it is consistent with the design, and it is the one place where "related product" came out as
"same category"; every other brand's product B is a different category (earbuds against a neckband, a razor
against a foam). Nothing in the repository says it was chosen so that two products would share evidence, and
it should not be described as if it were: it is the flavour variant that was picked. Consequence: R17 is the
single `different_product` repeat; it is marked in the per-request display above, where a reader can see
whether it carries the repeat margin by itself.

**Per-query fetch cap: 100, set 2026-10-28 (owner) as a SAFETY BOUND, not a design parameter.**
`research.max_pages_per_query` sits just above the 79 URLs (on 61 hosts) of the one measured search, so it
never binds in normal operation and bounds a pathological query that returns 500. **Untuned**, reported beside
every result with `queries_per_facet` and the template version, and revisited from the distribution once the
recording has logged its 153 searches. It is not set low to save fetch time: 24 seconds per search at
concurrency 8 is roughly 3 to 4 minutes of fetching per request, which is not a constraint.

### RULED 2026-10-28: the snapshot covers page bodies, not only search results (found the same day)

**Rulings (owner).** (1) **The pages are frozen.** 0.6 to 0.7 GB gzipped is cheap against the alternative, which is
that the one guarantee the arm comparison rests on does not cover the evidence the arms actually read. (2) **The
zero-retention rule is excepted, scoped** (`Architecture.md` section 6, amended): an evaluation namespace on disk
only, with a test that refuses raw bodies anywhere else; not in the repository (the repository holds the
manifest and hashes); retained on the laptop and one backup for as long as the thesis stands; not published or
shared. (3) **Failure freezing:** permanent outcomes (a 404, a robots refusal, a PDF skip) freeze as outcomes;
a transient failure is retried within the session, **bounded, with the count recorded** (`research.snapshot_page_retries`,
2, untuned; an unbounded "retry within the session" is the same non-termination in a smaller hat), then frozen as
failed and counted. **Frozen-as-failed is reported per request**: a request that freezes with 15% of its pages
failed gives all three arms thinner evidence, which is correct because it is constant, and must be visible and
not inferred from a gap. **A permanently failed page is itself evidence:** a cited source returning 404 is a fact
about that source; the outcome is frozen, not nothing. The text below is the analysis these rulings rest on.

#### Status: BUILT 2026-10-28 (`backend/app/research/snapshot.py`; 17 + 6 tests; not yet run on the live web)

*What exists.* A write-once disk store in an evaluation namespace (a directory with a marker file, refused if
inside the repository or not made as a store); `SnapshotSearch` and `SnapshotPages` that replay and never reach
the network (an unrecorded query or URL is `SnapshotMiss`); `RecordingSearch` and `RecordingPages` that
record, with a URL or query already in the store replayed and not refetched, so requests that share evidence
read the same bytes; `record_request`, which captures a request whole (its full ranked list, D2) or not at all:
entries are staged on disk and committed with the request's `committed` line written last, after the data is
fsynced, and a reader trusts only committed requests, so a crash at any point leaves nothing visible. Failures:
a permanent outcome is frozen as an outcome and replays as the same typed error; a transient one is retried at
most `research.snapshot_page_retries` times, the attempts recorded, then frozen as failed; the frozen-failed
share is in the capture report and the committed line. A degraded search is never recorded. Each entry carries
the seconds it took live (the unit costs LR is derived from). `manifest_summary()` is what the repository
holds: counts, a hash over the index files and the date range, never a body. *Not yet done:* a capture
against the real web, the backup copy, and the recorder's overall session loop (back off, resume, achieved
rate) around `record_request`.

*The driver has two modes and no default* (owner, 2026-10-28). `Mode.RECORD` aborts the request at once on
any throttled, unavailable or degraded search (`RequestAborted`; no page is fetched for a request that will be
discarded and redone). `Mode.PRODUCTION` follows `Rules.md` C4: it degrades to partial and DECLARES the gap
(`uncovered_facets`), so a live run that loses a facet to a throttle yields copy with a declared gap and not
nothing. A flag cannot set it wrongly: it is a required enum and anything else is a `TypeError`.

*The containment guard was shown to fail before it was trusted.* `tests/test_snapshot_containment.py` holds a
detector (`find_raw_bodies`: plain text or gzip, anywhere under a root, except the namespace) and three kinds
of evidence. (1) The detector finds a body planted in a non-evaluation path, a gzipped one, and a deeply
nested one, and ignores the namespace. (2) The real pipeline (record, commit, replay, the driver) is run with a
per-test random sentinel in every page body and the workspace, the whole repository and the pytest temp area
are scanned: bodies are in the store and nowhere else. (3) Two tests run the same check against a store
patched to leak a plain-text copy, and a gzipped copy, of every body to a stray path, and require it to raise.
*And the real containment test was run against the real source with a leak edited in* (a line in
`Staging.put_body` writing the body to `stray-copy.html` beside the store): it **failed**, naming
`.../workspace/stray-copy.html` ("raw page bodies found outside the evaluation namespace"), and passed again
once the line was removed (checked: no mutation marker left in the file). Two defects in the first draft of the
test were found by the detector itself: the sentinel was a literal in `tests/snapshot_support.py`, and
other tests' planted sentinels sat in the shared pytest temp area; each test now makes its own sentinel at
run time. The recording-path import guard also made the new modules join `RECORDING_MODULES`, and shows the
recording path reaches no encoder.

#### First live capture (2026-10-28; R01, into a disposable trial store; `evaluation/snapshot_trial/capture-20261028T111403Z.json`)

The snapshot had never touched the real web, so one request was captured end to end: 8 queries on the four
declared engines, real fetches (robots.txt honoured, politeness on, the real extractor), real freeze, then a
replay compared with what the live sources returned. Every body and every search answer was hashed as the live
source returned it, not as the store holds it, so the check is not circular.

**Replay: byte for byte.** 8 of 8 search answers, 252 of 252 page bodies (SHA-256) and 67 of 67 frozen failures
(same typed error, kind and status) matched. The committed line and the replayed driver agree with the capture.
*What "byte for byte" means:* the store freezes the page as the fetcher DECODED it (UTF-8), not the wire bytes;
2 of 252 bodies (0.8%) contain a replacement character from a lossy decode, so the original bytes of those
two cannot be recovered from the snapshot. The comparison is exact against the decoded text every arm reads.

**What the real web did that the mocks did not:**
- **Pages per request: 319, not 616.** The driver asks for 50 results a query (the provider's maximum), so 8
  queries gave at most 400 URLs and 319 distinct ones (209 hosts; flipkart.com 13, amazon.in 10). My earlier
  figure of 77 URLs a query came from a search asked for 50 plus merging, and was never the number a request
  sees. **Storage is therefore smaller:** the 252 fetched pages are 19.1 MB gzipped (106.5 MB raw, 5.6x, 76 KB a
  page, median raw body 265 KB, largest 1.86 MB), about 19 MB a request; the whole recording is about 0.4 GB
  gzipped, not the 0.6 to 0.7 GB estimated above.
- **21% of the request's pages froze as failed** (67 of 319), against none in the 79-URL search measured earlier
  (a regulator-heavy query): 21 HTTP 403 (15 hosts that refuse an identified research client), 15 robots
  refusals, 11 too large (over the 2 MB cap: refused, not cut), 8 robots.txt unreachable (7 with no status, 1
  with 503), 5 HTTP 503, 3 PDFs (0.9%, not the 15% of the regulated query), 2 HTTP 404, 1 HTTP 410, 1 DNS
  failure. This is the frozen-as-failed share the owner asked to see per request; it is constant across arms and
  makes every arm's evidence thinner by the same fifth.
- **Retries rarely helped.** 27 retries were spent on 14 transient failures; 1 cleared, 13 stayed failed after
  the bound (8 of them robots.txt that would not load, 5 server errors). The bound did its job; the retries mostly
  bought nothing.
- **Only half the URLs became evidence.** Replayed through the driver: 252 pages served, 44 refused by the
  extractor (too little main text), 48 discarded below the acceptance threshold, **160 accepted findings of 319
  URLs (50%)**.
- **A search takes about 5 s, not milliseconds**: the engine waits out its per-engine timeout (5 s) for the
  engines that never answer (avalw, seznam, tusksearch and others among the 22 then enabled; 17 now, see the second capture below), so every answer takes
  the full 5 s whatever the four declared ones do. Harmless at 2-minute gaps, and a number the derived web time
  must use (median 5.0 s, max 5.2 s).
- **8 of 8 searches clean, none degraded**: the first live observation of the degraded-search rate, 0 of 8. At
  n = 8 the 95% upper bound is about 31%; it is a datum, not a measurement. Capture span 839 s (7 gaps of 120 s),
  one attempt, no refusal. The whole request took 1,010 s: 840 s of gaps, about 40 s of searching, about 165 s
  fetching 319 pages at concurrency 8 (median page 2.0 s, 90th percentile 6.8 s, slowest 62.9 s including retries).

**A defect only the real run could show, fixed the same day.** The unit costs stored with each search were about
120 s each, not 5 s: the pacing wait sat inside the timed section, so the seconds-per-search that LR's derived
web time is computed from were the gap, not the search. The wait is now outside the timer (`Pace` hooks on
`RecordingSearch`), and a page's recorded seconds now exclude the retry backoff too. Two regression tests; the
search one was shown to fail against the old ordering (it recorded 0.313 s where 0.15 is the bound). The trial
store's own `elapsed_s` values are the old, wrong ones; it is disposable and was not used for any figure.

#### The analysis (found 2026-10-28)

**What the design does today.** The snapshot ruled above freezes SEARCH RESULTS and replays them for every
arm. The arms then fetch the URLs, and the evidence an arm reasons over is the page (the extracted text,
scored), not the search result (a URL and a snippet). Pages come through the page cache
(`app/research/page_cache.py`): Redis, keyed by canonical URL, **TTL 3600 s** (`research.page_cache_ttl_s`),
modes `use` and `bypass` only. The snapshot mode does not exist for either cache. Arms of a request run up
to 3 days apart, so an arm that runs more than an hour after another refetches live: the same URL can return
different content, a different error (a timeout, a 403, a changed robots.txt) or be gone. **So the
held-constant guarantee covers search and not fetch**, and the arms of one request are not guaranteed to
have reasoned over the same pages. A longer TTL would not freeze anything: expiry, Redis eviction and
overwrite on refetch are all still possible.

**What freezing pages would take (measured where it could be).** Scale: about 616 pages per request at 8
queries and 77 URLs each (at most 800 under the cap of 100); about 12,100 URL slots over the 153 distinct
searches, of which about 85% fetched in the measured search (about 10,300 pages, an upper bound before
dedupe). Size, from the 67 fetched pages of the one measured search and a re-fetched sample of 20: raw body
mean 295 KB (median about 200 KB, largest 1.7 million characters); **gzip 5.2x, about 57 KB a page**; the
extracted text, which is all an agent sees, mean 5,400 characters (the extractor refused 4 of the 20 for
too little main text). (Superseded by the first live capture below: a request has 319 pages, not 616, and the whole recording is about 0.4 GB.) So **about 0.6 to 0.7 GB gzipped for the whole recording** (3.0 to 3.7 GB raw), about
35 MB a request; the extracted text alone would be about 56 MB. Time: a search's pages took 24 seconds at
concurrency 8, and a search is followed by a 2-minute gap, so the fetching fits inside the waits and adds
about nothing; done separately it is about an hour.

**Can the page cache be put in record-then-replay mode like the search cache?** Yes in shape, no in place.
`PageSource` is a protocol, so a snapshot is two more implementations of existing protocols (a recording
`PageSource` that fetches and writes, a replaying one that only reads), and the driver does not change.
**It cannot live in Redis:** 0.6 to 0.7 GB gzipped is more than the roughly 1 GB the host has spare with a
run in flight, and Redis is RAM. It is a **disk store**: gzipped bodies named by content hash plus a manifest
(canonical URL to hash, status, final URL, content type, fetch time, redirects, or the typed failure), write
once, never expired, never overwritten, in its own namespace, outside the repository (third-party content;
the repository holds the manifest's hash and date range, as for the search snapshot), kept on the laptop plus
one backup. **Keyed by canonical URL across the whole recording, not per request:** requests that share a
query share its URLs, and campaign 3 must read the same bytes as campaign 1 (the repeat-evidence ruling
above); per-request copies would let them drift apart. The cost: a page's age is that of the first request
that fetched it, so the "age of the oldest replayed entry" report covers pages as well as searches.

**This is an exception to the zero-retention rule, and needs the owner's ruling to be one.** `Architecture.md`
section 6 puts raw page bodies in Redis under a TTL "and nowhere else"; its purpose is the privacy boundary
for customer data. These are public pages fetched by queries made of category terms, in an evaluation store
that production never uses; but the rule as written forbids it. Proposed: state the exception in section 6,
confine it to the evaluation namespace, and add a test that refuses to enable it outside it.

**Does the atomic-per-request rule extend to it? Yes, and it should.** A request is committed when its
searches AND its pages are all recorded, in one unit; a partial capture is discarded; the manifest is
resumable at request granularity. *One thing the search rule does not settle:* a degraded search is retried
and never recorded, because a throttle is an infrastructure artefact. A page can fail for a permanent reason
(404, 403, robots disallows it, a PDF, not HTML, too large), which IS evidence and should be frozen so every
arm sees the same refusal, or for a transient one (timeout, network error, 5xx). Retrying a whole request
until every one of about 600 pages fetches would never terminate. Proposed: retry a transient failure a
configured number of times inside the capture session, then freeze the final outcome, failed and with its
kind, and report the share of frozen-failed pages per request.

**What it does to the design.** Fetching moves into the recorder; the arms never touch the network, and
`SnapshotSearch` and `SnapshotPages` sit behind the same two protocols the live pipeline uses, so the driver
(search, dedupe, fetch, extract, score) is one piece of code run live or replayed. Extraction stays at
replay time on the frozen HTML (pinned trafilatura, rerunnable, inspectable), rather than freezing its
output. **One consequence for the metrics:** replay removes web time from every arm (searches already do),
so LR cannot show a time saving from fewer searches or fetches; it would count them (searches and fetches
avoided) and report the recorded capture time separately.

*Second run: STOPPED, not finished (2026-10-28, `evaluation/search_rate/hold-20261028T064809Z.jsonl`).* The
run-to-completion hold was killed by Claude Code's memory-pressure reaper after **45 searches over 91 minutes**
(06:48:12 to 08:19:15 UTC; gaps 123 to 125 s, no late ask; no summary line, so the outcome is neither `held`
nor `collapsed`). It certifies nothing about 4 hours or 120 searches. What the 45 searches show, as far as
they go: **yep, yandex, yahoo and duckduckgo web answered with rows in all 45; privacywall and swisscows each
failed once (a timeout, searches 18 and 15) and mojeek returned zero rows with no error twice (searches 2 and
37); no engine reached 3 failures in a row, so none fell, and at least 5 of the 7 answered in every search.**
No refusal of the "too many requests" kind appeared for any of the seven. Memory available on the worker host
fell from about 3 GB to 1.25 GB around 08:05 to 08:10 and was 2.07 GB at the last search; the SearXNG
container held 280 MB, and a full test-suite run, mypy and ruff were being run on the same machine during the
run, which is a plausible contributor and is not established as the cause. **Not rerun (owner, 2026-10-28):** the 45 searches produced zero rate refusals for any of the seven; the
three failures were two timeouts and a silent empty (service faults, not throttles); four more hours would
mostly re-confirm that, and the recording run is a better measurement because it is the real workload and
produces the artifact: 153 distinct searches at 2-minute gaps is about 5 hours, the adaptive recorder backs
off, resumes and logs its achieved rate, so the first recording session measures the sustainable rate while
doing real work. **What the 45 searches establish and do not:** these seven sustained 2-minute gaps for 91
minutes with no rate refusal. **Nothing is established about 4 hours or 120 searches.** The run was killed by
memory pressure on the host, not by any engine (rule on shared use of the machine: `Architecture.md` section
11).

### The declared engine set (DECLARED 2026-10-28, owner)

**Declared: yep, yandex, yahoo, duckduckgo web** (`research.declared_engines` in `config/scoring.yaml`). The
rule it serves (section 8 above): "degraded" means a DECLARED engine failed, a degraded search is never
recorded into the snapshot, and the harness refuses to start while the set is undeclared. **Evidence:** the
run-to-completion hold, `evaluation/search_rate/hold-20261028T064809Z.jsonl`, 45 searches over 91 minutes at
2-minute gaps (stopped by memory pressure, not by an engine): these four answered with rows in all 45 with no
failure of any kind. privacywall and swisscows each failed once (a timeout) and mojeek returned a silent empty
twice; no rate refusal for any of the seven. **Enabled but NOT declared:** privacywall, swisscows, mojeek,
brave and every other enabled engine.

*Why four and not seven.* A declared engine's failure degrades the search, so each flaky declared engine
multiplies the retries a recording needs. Declaring all seven would have degraded 4 of those 45 searches
(about one in eleven) on transient faults alone, for engines whose results arrive anyway when they work.

*Two conditions travel with it.* (1) **Every search records how many undeclared engines contributed rows,
and which** (`SearchRecord.undeclared_seen`, `.undeclared_contributing_count`), otherwise an undeclared
engine silently failing makes a search look clean with thinner evidence (decision log 7.2 at another
level). (2) **Every result reports both numbers**: the declared count and the count that actually
contributed (`SearchEvidenceReport.declared_count`, `.contributing_count`, `.engines_line`, for example
"4 declared; 6 contributed (undeclared: privacywall, swisscows)"). "Four engines" alone would understate
the evidence base and "fourteen" would overstate it.

*An unobserved failure is not a zero rate.* The four declared engines showed no failure in 45 searches. By
the rule of three that puts a 95% upper bound of about 6.5% on the per-search failure rate of any one of them
(3/45 = 6.7%; the exact one-sided bound is 6.4%). It is a bound, not an estimate. Across four engines, IF
their failures are independent and each sits at the bound, up to about a quarter of searches (23 to 24%)
could degrade; if they failed together it would be about 6.5%. The range consistent with the evidence is
therefore **from about none up to about a quarter of searches degrading**, and the expectation for the
recording is that range, not "no degradation expected". The bound also rests on one 91-minute stretch of
one day and carries nothing about another day. **The adaptive recorder absorbs degradation (a degraded
search is retried, never recorded), so a high rate costs time and not correctness. Its actual rate is the
measurement: the first recording session tracks the degraded-search rate (degraded searches over searches
issued, and which declared engine caused each) and reports it; 45 searches is a bound, not the answer.**

*Not established.* Which contributing engines share an upstream index (URL containment in one search is the
only evidence; the image documents a backend for two engines); anything about 4 hours or 120 searches; a
rate for any day but 2026-10-28. *Recording arithmetic under this set:* 153 distinct searches (240 query
slots), recomputed against the declared set in `tests/test_evaluation_test_set.py`: the engine set is part
of the cache key, so a different declaration is a different 153 and re-records everything.

**Second live capture of R01 (2026-10-28, after the engine, page-cap and agent changes; a fresh disposable
store, `evaluation/snapshot_trial/capture-20261028T125439Z.json`).** Changes made BEFORE it, owner's rulings:
the six engines that answered 0 of 8 searches in the burst (google, qwant, startpage, dogpile, searchmysite,
tusksearch) were disabled in SearXNG (`GET /config` now lists 17 enabled), and `research.fetch_max_bytes` was
raised from 2 MB to 5 MB. The user agent was changed AFTER this capture started (below), so its 403s were drawn by
the OLD agent string. The first trial's stored unit costs were wrong (timer bug) and the store was deleted.
- **Replay: byte for byte, again.** 8 of 8 search answers, 270 of 270 page bodies, 60 of 60 frozen failures.
- **The 153 does not move.** `cache_key` hashes the DECLARED set (duckduckgo web, yahoo, yandex, yep), not the
  set SearXNG runs, so disabling undeclared engines changes no key; recomputed by
  `tests/test_evaluation_test_set.py`: 240 slots, 153 distinct keys. (Changing the declared set would still
  re-record everything.)
- **The 5-second search did not go away.** Median 5.03 s, max 5.42 s, as before: `avalw` still times out
  (observed in a probe search, which also showed brave and wikipedia suspended for too many requests, plain
  duckduckgo behind a CAPTCHA and gmx failing to parse, all of which fail fast). `avalw` answered 3 of 8 in
  the burst, so the zero-of-eight rule did not remove it; whether to disable it is open. It costs about 40 s a
  request and nothing else.
- **The 5 MB cap works.** 0 of 330 pages too large (largest 3.6 MB; 11 of 319 were refused at 2 MB). Of the 11
  formerly refused pages, 9 extract to real text (775 to 19,994 characters; e.g. the boAt blog index at 4.98 MB, three
  gonoise.com product pages), 2 do not (portronics collection, gonoise.com home): the cap was costing evidence
  and stays at 5 MB.
- **Failures: 60 of 330 frozen failed (18.2%, was 21%):** HTTP 403 x25 (7.6%), robots refusal x14, robots.txt
  unreachable x10 (3.0%), HTTP 503 x5, HTTP 404 x2, PDF x2, 410 x1, DNS x1.
  - **403s are the price of asking honestly.** The agent string was `BrandverseResearch/0.1 (academic research
    project)`: a product token and version, but no contact URL. Changed (owner) to
    `... (academic research project; +https://github.com/Datta23-s/brandverse-ai)`, a repository URL, not an
    email address. It is not a browser string and will not become one: a host that refuses an identified
    research client has said no, and that share is the cost of asking honestly. The 403 rate under the new
    string is not measured yet; the recording will be its first measurement.
  - **robots.txt unreachable is kept as it is** (owner): treating an unreachable robots.txt, a 5xx, as disallow
    is what RFC 9309 specifies. Its measured cost: 8 of 319 (2.5%) in the first capture, 10 of 330 (3.0%) in the second.
- **A request's real evidence base is about half of what search returns, and a reader should see both numbers
  together.** 330 URLs became 176 accepted findings (53%; first capture 319 to 160, 50%): 60 URLs failed to
  fetch (18%), 36 were fetched but refused by the extractor for lacking main text (11%) and 58 were discarded
  below the acceptance threshold (18%). This holds for every arm that researches (Baseline A does not), so it
  is not a confound between arms; it thins what each of them can use by the same half.
- **The recorder's degraded-search path ran for real.** Query 2 of the first attempt came back degraded (yahoo
  failed): the request was aborted, the clean search was held and carried into the redo, the gap doubled to
  240 s, and attempt 2 committed. Degraded searches: 1 of 9 live (11%), 1 of 17 across both captures (6%), inside
  the registered expectation (none to about a quarter). Cost: capture span 1,768 s against 839 s, session 2,109 s.
- Storage: 25 MB for one request (270 pages), consistent with the 0.4 GB estimate for the whole recording.

**avalw disabled, and what the per-search unit cost is (owner, 2026-10-28).** Every search took 5.03 s because
`avalw` timed out on every one, and those per-search seconds are the unit cost the derived web time is
computed from (LR = count difference x unit cost). A dead engine's timeout inside the unit cost would inflate
the claimed saving systematically, in the direction that flatters the Proposed arm, so `avalw` was disabled
(removed from `keep_only` and the `engines:` block, container restarted; the zero-of-eight rule did not cover
it, but this reason does). Four probe searches through the JSON API afterwards took 3.0 to 3.6 s with no
engine timing out (brave and wikipedia suspended for too many requests, duckduckgo behind a CAPTCHA, gmx
failing to parse: all fail fast); the remaining 3 s is the slowest ANSWERING engine, which is the cost
that belongs in the unit cost. These four are not the unit cost: **the unit cost is taken from the next
trial's and the recording's stored per-search seconds**, which have the pacing wait outside the timer.
**The unit cost is configuration-dependent**: it is the cost under the declared set (duckduckgo web, yahoo,
yandex, yep) and the enabled list as recorded (16 engines after this change: bing, brave, crowdview, duckduckgo, duckduckgo web, gmx, mojeek, mwmbl, privacywall, seznam, swisscows, wiby, wikipedia, yahoo, yandex, yep, in `infra/searxng/settings.yml`),
and not a property of searching; it is reported with both, and a different enabled list is a different unit
cost.

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

