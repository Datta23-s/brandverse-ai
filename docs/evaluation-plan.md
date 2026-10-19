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
2026-10-17). Steps append to an in-process `MemoryStage`; it is inserted in the same transaction
that writes the run's terminal status, and only when the run ends `completed` (for an experiment
run, also only if the Phase 10 primacy check passes inside that transaction). Every other ending
discards it; a process that dies takes it with it. Nothing reaches `memory_items` before that
point. `backend/tests/db/test_memory_staging.py` stages a write and then ends the attempt with each
of the four infrastructure failures — rate-limit rejection, daily-quota exhaustion, a network
error, and a process crash (a cancelled attempt, and a worker process killed outright) — and
asserts `memory_items` is unchanged each time; controls show a completed run commits, a
budget-stopped run commits nothing, a completed run whose campaign no human approved commits
nothing (its commit is refused and it ends `failed`; `Memory.md` §6.1, as built 2026-10-19), and
status and memory land together or not at all. Once a pair
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
| LR  | `(T_baseline − T_proposed) / T_baseline × 100` |

Reported additionally, broken down by campaign position (1st / 2nd / 3rd per brand), since
that is where the memory effect is expected: depth distribution (D0/D1/D2), memory hit rate,
coverage, reuse depth (`Memory.md` §8). Strong-tier calls per arm are reported as a result
(threat V1, below).

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
   escalate only where T4 or the high-risk rule requires it. In the test set that is the regulated brand's 3 requests (B07,
   R19–R21) and the 3 requests on products with entered evidence conflicts (B01, R01–R03), so at
   most 6 of the 30 Proposed runs should touch Strong at all.
2. Every Strong call is logged per arm in `llm_calls` (`tier`, `route_reason`), so the asymmetry
   is **quantified and reported**, not assumed away.
3. **Strong-tier call counts per arm are reported as a result in their own right**: total, per
   request, and by reason (T4, high-risk, conflicting evidence). Quality comparisons between B and
   Proposed are also reported with the Strong-touching requests separated out, so a difference that
   exists only there is visible as such.

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

**Who writes what, and in what order (decided 2026-10-18).**

1. The developer writes a one-page **brief**: three fictional brands, their sectors, product lines
   and rough positioning (`evaluation/relevance/brief.md`). The labeller sees the brief.
2. The developer drafts the **corpus** from the brief, then freezes and hashes it. **The labeller
   never reads the corpus text before labelling.**
3. The labeller (Kundan) writes the **50 queries from the brief only** — plausible marketing
   questions about brands whose shape is known, not questions copied from documents.
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

