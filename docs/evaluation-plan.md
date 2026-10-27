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
| LR  | `(T_baseline − T_proposed) / T_baseline × 100` |

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

**Ruled 2026-10-27 (owner): 50 HEAD requests, all on the regulated group, none on the general one.**
The general group's free floor (1 of 96 by extension alone) already answers it and needs no request;
the open question is only whether regulated PDFs are a real share, and 8.3% from nine searches
already suggests they are, so the requests are spent where the answer is in doubt
(`--sample 50 --sample-group regulated`; the general group is reported as a floor, never as an
estimate, since nothing in it is sampled). **Not run yet:** it waits for the ramp to finish and a
fuller URL log, as instructed.

### The recording is captured request by request, atomically, and is resumable (owner, 2026-10-27)

**The rule: a request is captured atomically or not at all.** If a session dies partway through a
request's ranked query list, that request's partial capture is **discarded** and the request is
redone from its first query in the next session. Resumability works at **request** granularity, not
search granularity, and the manifest tracks completed *requests*, not completed searches. *Why:*
each request's snapshot is internally coherent, captured within one session, and the 3-day window
(above) runs cleanly from that one capture time. A straddled request would have query 1 from Monday
and query 3 from Wednesday, and the analysis would then be reasoning about staleness *within* a
request as well as between arms. *Cost:* a handful of redone searches, at most the clean ones
already done for the request in flight. This closes the question left open earlier of how a request
that straddles sessions is treated against the window: **there is no such request.**

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
6. **The recording stops cleanly when the engine does.** A throttle that persists past the recovery
   time measured by the ramp ends the session with the manifest intact, the in-flight request
   discarded, and a stated resumption point, not a crash. Requests are recorded in the test set's
   order, one at a time.
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

