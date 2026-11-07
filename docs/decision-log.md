# Design Decision Log

**BrandverseAI — adaptive agentic marketing intelligence**
Raw material for the methodology chapter.

**Revision 2 (2026-10-27).** Revision 1 was verified line by line against the repository and 17
discrepancies were found. All are corrected here. The nature of several is recorded in §10,
because a document whose subject is not overstating what a system does should not be exempt from
its own rule.

---

## How to read this

Every entry records a decision, the date it was fixed, the reason given at the time, and what it
cost. The dates matter more than the decisions. A thesis that reports metrics is asking a reader
to believe the metrics were not chosen to suit the results, and the only evidence for that is a
record showing when each rule was fixed relative to when the data existed.

**Every entry carries a status:**

- **BUILT** — implemented, tested, and in the running system.
- **DESIGNED** — ruled and recorded, not yet implemented. The rule exists in documentation; nothing
  enforces it.
- **OPEN** — a known problem with no agreed fix.

The distinction is not cosmetic. Several rules below are enforced by database constraints or tests
and cannot be bypassed; others are written down and depend on whoever builds the component next.
Describing the second kind as though it were the first is the exact failure this project has
caught in itself three times (§7, §10).

**Entries marked [superseded]** record a decision later changed, with the reason. They are kept
rather than edited away: a record showing only the surviving decisions is a reconstruction.

**What has and has not been measured, as of 2026-10-28.** No evaluation run has been executed and
no metric defined in the evaluation plan — ARR, SRR, CR, LR, Precision@10 — has been computed.
Measurements that *have* been taken are: the lexical echo comparison between two corpora (§4.2),
the diagnostics on a discarded labelling export (§4.5), storage per row, the search-rate probes and the
fetch cost of one search (§6), and how many distinct research situations the 30 requests give the
query planner (§1.7). Two freeze-script thresholds were set *after* seeing the discarded export, which is
stated where it occurs (§4.5) rather than glossed.

---

## 1. Evaluation design

### 1.1 Experiment membership is opt-in — **DESIGNED, not built**
**Ruled 2026-10-17.** Recorded in `Architecture.md` §9 as *designed, approved, not built (Phase 10)*.

Runs belonging to an evaluation will carry an explicit `experiment_id`, a request reference and an
attempt number, set only by the harness's own backend role. A run without those markers is excluded
from every evaluation read. No migration creates these columns yet; the harness does not exist.

*Reason.* The first harness design selected runs by organisation id and then subtracted test and
practice flags. Under that design a stray manual run, a debugging run or a re-run inside an
evaluation organisation was counted. Several separate instances of evaluation-irrelevant data
reaching evaluation reads had already been found and patched individually; each fix was correct
and each was reactive. Under opt-in, forgetting a marker removes data rather than adding it, so
that class of error cannot recur.

*Conditions attached to the design.* The arm is forced by the same role, so a run cannot be
mislabelled between arms. An experiment row becomes immutable once any run references it, so its
definition cannot change mid-evaluation. A uniqueness rule on experiment, request, arm and attempt
makes re-runs explicit rather than silently double-counted.

### 1.2 The attempt rule — **DESIGNED; classifier BUILT**
**Fixed 2026-10-17, before any evaluation run existed.**

All attempts are reported. For each request–arm pair the primary result is the **first attempt
that terminated without infrastructure failure**. Infrastructure failure is defined narrowly and
in advance: rate-limit rejection, network error, process crash, daily-quota exhaustion. Nothing
else qualifies.

A run that completed and produced poor, blocked or empty output is a **result**, not a failure. It
counts as the primary attempt and is reported as-is. A method that blocks its own run is data
about the method.

*Reason.* Without the narrow definition, "it failed, I re-ran it" quietly becomes "I kept the run I
liked."

*Build status.* The classifier that determines which of the four infrastructure failures ended an
attempt exists in code and its result is logged. The persistence of that classification, and the
first-qualifying-attempt selection across attempts, belong to the harness and are not built.

*Consequence that IS built.* Only a qualifying attempt may write back to memory. Because primacy is
unknown until an attempt terminates, memory writes are staged in the run's own process and
committed in the same transaction that records the run's final status. Deletes on `memory_items`
are revoked, so a write that should not have happened could not be removed afterwards — the
staging design is what makes the rule enforceable rather than aspirational. Tests kill an attempt
with each of the four infrastructure failure types and assert `memory_items` is unchanged.

### 1.3 Search is held constant across arms — **DESIGNED**
**Ruled 2026-10-26.** The query planner does not exist yet.

The planner runs once per request and emits one ranked query list. Every arm that researches draws
from that same list; depth selects a prefix of it. D0 takes none — Baseline A performs no research
by design, so "the same query set" means the same *available* set, not that every arm issues every
query.

*Reason.* If arms issue different queries, a difference in output could come from the memory
mechanism or from having seen different evidence, and cannot be attributed. Holding the available
evidence constant isolates what the thesis claims to contribute.

*Conditions.* The ranking is computed by the shared planner from the request alone, never by the
Proposed arm's own logic — otherwise the prefix is privileged. Each run records which depth it
chose and on what basis, because "adaptive depth" is a claim requiring evidence that the adaptivity
actually varied; if the Proposed arm selects maximum depth every time, the result reduces to "more
search is better" and must be reported as such.

*Secondary benefit.* Recording cost falls from 30 × q × 3 searches to 30 × q.

### 1.4 Evaluation search uses one frozen snapshot — **DESIGNED**
**Ruled 2026-10-25, amended 2026-10-26 and 2026-10-27.** Snapshot mode is not built; the search
cache currently has only use and bypass modes.

Search results are recorded once and replayed for all arms. Entries do not expire or get
overwritten during the evaluation.

*Window.* All arms of a request run within 3 days of that request's capture, per request rather
than globally, because the comparison is between arms of the same request. **The requirement is
that the harness refuse an arm outside the window.** The harness does not exist, so nothing
enforces this today.

*Atomicity (2026-10-27).* A request's queries are captured in one session or not at all; a partial
capture is discarded and redone. This removes the class of question about a request whose snapshot
straddles two days.

*Degraded searches are never recorded.* A snapshot containing a degraded search makes every arm
replay that degradation, permanently and invisibly.

*Adaptive recording (2026-10-27).* The recorder issues a search, backs off on refusal, retries with
increasing delay, resumes when clean, and records its own achieved rate. It targets a measured rate
but does not depend on that rate being correct — see §6.

*Residual, stated.* A query first issued late in the recording window sees a later web than one
issued early. Narrowed, not removed.

*Gap found 2026-10-28 — OPEN, not ruled.* The snapshot freezes search results and not page bodies. The
arms then fetch the URLs through a TTL page cache (one hour), so the arms of a request can reason over
different content for the same URL; the held-constant guarantee covers search and not fetch. What freezing
pages would take (about 0.6 to 0.7 GB gzipped on disk, an exception to the zero-retention rule, the same
atomic-per-request rule) is in `docs/evaluation-plan.md` section 8.

#### 1.4a The atomic capture rule was relaxed to session-scoped reuse — **BUILT** (disclosed 2026-10-28)
The rule "a request is captured atomically or not at all" (ruled 2026-10-27) was relaxed on 2026-10-28
*after seeing its cost in the session loop*, and before any live recording data existed (the cost was seen in
the loop's design and its tests). A clean search captured for a request is now reused by a later attempt at the
same request within the same session; nothing is reused across sessions. *Reason.* The rule existed so a
request's queries are captured close together; within a session the worst case (the 10-hour wall limit)
is far inside the 3-day arm window, so discarding clean searches bought a tighter guarantee than the design
already accepts. *Cost of the change, stated:* the coherence is now reported, not guaranteed by construction;
each request's committed line carries its capture span and the recording reports the maximum and median.
`searches_wasted` now measures cross-session loss only. (`docs/evaluation-plan.md` section 8.)

#### 1.4b The pacer backs off for a rate refusal only — **BUILT** (disclosed 2026-10-28)
The recorder's pacing gap stretched on ANY degraded search (ruled 2026-10-27, item 1 of "the recorder is
adaptive": "a degraded answer is a refusal here"). From 2026-10-28 it stretches only on a **rate refusal** —
a declared engine saying "too many requests" — and any other degradation retries the request at the
**unchanged** gap. The abort-and-redo of the request is untouched; only the session's pace is. Rate-refused
and degraded-but-not-throttled searches are counted apart in every session report.
*Changed after seeing its cost, and stated as such.* The first recording session was stopped eleven minutes
in (started 15:17:42 UTC, stopped 15:28) with **nothing committed**, so no recording data existed; the change
was made and the recording restarted at 15:36:35 UTC. The cost that prompted the look: at the trials' 8%
degradation rate the old rule drives the gap to its 900 s ceiling, giving a simulated median of about 35
hours for the recording against 5.8 hours clean.
*Why it is a defect, not a relaxation.* The two signals were already distinguished one layer up, in the
search provider, from the day it was written (`SearchRateLimited` only for "too many requests",
`SearchUnavailable` for every other failure); the pacer called one method for both and discarded the
distinction. A service fault from one of sixteen enabled engines is not evidence about how fast this IP may
ask. The backoff has still never had a legitimate trigger: every degradation observed so far (three trial
captures and the stopped session) was a single engine failing — yahoo, then yep — and nothing was ever
rate-refused. *What it does not touch:* the queries, the engine set, the fetching, the freezing — the pacing
is not part of the captured evidence. (`docs/evaluation-plan.md` section 8.)

#### 1.4c The wait between attempts backs off for a rate refusal only — **BUILT** (disclosed 2026-10-29)
The wait before redoing an aborted request (60 s doubling per consecutive abort, to 1800 s) is now lengthened
only by a **rate refusal**, by the same shared test as the gap; any other abort redoes the request after the wait
already in force. Counted apart (`aborts_rate_refused`, `aborts_fault`); each attempt line names its kind.
*Changed after seeing its cost, and the earlier decision is recorded with it.* It was reported as the same type
confusion on 2026-10-28 and the owner declined to change it that night, on a cost estimate that assumed transient
faults. The first full session then met a sustained outage (yahoo, from 18:40 UTC): seven attempts at R13 in 104
minutes, the wait doubled to its ceiling, and the dark-period ceiling ended the session with 12 of 30 requests
committed. Unlike 1.4b, recording data exists this time (12 committed requests, verified byte for byte); the change
touches none of it nor anything still to be captured. *Not changed:* the dark-period ceiling, which still ends a
session if one declared engine stays down for 90 minutes. The session report's failed-page share now divides by
pages fetched live (`frozen_failed_share_of_fetched`, 22.5%), not by all pages in the requests (12.9%, the wrong
figure, whose key is removed). (`docs/evaluation-plan.md` section 8.)

#### 1.4c A search is complete with three of four declared engines — **BUILT** (disclosed 2026-10-29)
**Ruled 2026-10-29 (owner).** Complete for recording = at least three of the four declared engines answered with
rows (was: all four, none named failed). *Not simply a relaxation:* the old test passed a search where an engine
silently returned nothing without being named as failed (R01 holds one), so it never enforced four-of-four; the new
test counts engines that contributed rows. Reason: one chronically failing engine (yahoo) stalled the recording for
2 h 49 m with zero captures. The declared set, the cache key, the pinned SearXNG image and R01-R12 are untouched;
the entry now records which declared engines contributed and which failed, and the manifest and capture report give
the distribution (stored today: 62 searches with four, 1 with three). R13-R30 are captured during the yahoo
failure, so the snapshot is inhomogeneous, constant across arms. Disclosed with the date it was made, before any
R13-R30 data exists. (`docs/evaluation-plan.md` section 8.)

### 1.5 The authority prior is reported twice — **DESIGNED**
**Ruled 2026-10-25.**

The headline result is to be reported with the source-authority prior as configured, and again with
authority flattened to 0.5 for every host. Both settings were fixed before any data existed.

*Reason.* The prior's values are the developer's untuned judgement with no citation behind them. If
the comparison between arms survives flattening, the prior is not carrying the claim and that can
be stated in one sentence. If it does not survive, that is a finding to have before a viva rather
than during one.

### 1.6 Retrieval-depth sweep — **DESIGNED, and currently blocked**
**Fixed 2026-10-18.**

`hnsw.ef_search` sweep values fixed at 40, 100, 200, 400, with the expectation recorded in advance
that at the planned corpus size the sweep will be **insensitive** — HNSW against roughly 120 rows
is close to exhaustive, so the index barely approximates. A flat result confirms the measurement
rather than the parameter.

*Blocked.* The sweep is evaluated by Precision@10, which is not reported (§4.5). It cannot run
until a valid labelling exists. The pre-registration stands; the measurement does not.

### 1.7 Repeat campaigns share their research evidence — **DESIGNED** (the planner and the tags are BUILT)
**Ruled 2026-10-28.** The query planner reads the product's generic category and the audience, never the
objective, so requests on the same category issue the same queries (except the audience one) and are
replayed the same recorded evidence. 30 requests give 19 distinct category situations; 11 repeat an earlier
one (`scripts/planner_variety.py`).

*Reason.* Putting the objective in the query text would make a repeat campaign see different evidence from
the first campaign on the same product, so the Proposed arm's advantage on it would mix "it remembered" with
"it saw new evidence". Holding the evidence constant is what isolates memory from fresh evidence; changing
the categories to diversify it does the same more weakly.

*Registered with it.* A three-way split of every primary metric by evidence stage (cold start 10, warm novel
9, repeat 11; position-1 requests have no memory by construction, so a two-group split could mean only "warm
beats cold"), with per-request margins shown. *Prediction, asymmetric in what it can show:* repeat margin >
warm-novel > cold-start. Confirmation is reported as **consistent with** a working mechanism, a reversal as
**evidence against** it, and this is on record before any data (`docs/evaluation-plan.md` section 8). *Stated
beside it:* the planner's query generation is not adaptive; the 153 distinct recorded searches hold only for
one declared engine set, and none is declared yet.

---

## 2. The memory mechanism

### 2.1 A memory item is one fact and is never split — **BUILT**
**Ruled 2026-10-19.** An item over 400 tokens is refused, not chunked. This superseded an earlier
specification of 200–400-token chunks with 15% overlap. **[superseded]**

*Reason.* Chunking with overlap is a document-retrieval technique for prose. A fact split across
two chunks produces two half-facts, each retrievable and citable on its own, with the qualifier
left behind in the other half — the entailment gap of §7.1, self-inflicted.

*Consequence.* Refusals are visible: a log line naming the run, and an audit row per run listing
each refusal with its reasons and token count. A silently dropped candidate is a memory that should
exist and does not, which nobody would ever find. Content of candidates refused as personal data is
withheld from both the log and the audit row.

### 2.2 Approval gates increases in trust; corrections do not — **BUILT**
**Ruled 2026-10-19.**

Human approval gates anything that **adds or reinforces** trust: new memory items, reuse confidence
bumps. Corrections that **reduce** trust — decrements, revocations — commit regardless of the
campaign's outcome.

*Reason.* Requiring approval before a bad memory can be corrected means the memories that caused a
rejection are precisely the ones that never get fixed.

*Consequence, stated explicitly because the natural reading drops it.* A run that ends `failed`
because no campaign was approved has still terminated without infrastructure failure, so under the
attempt rule its decrements commit. A test covers exactly this: reviewer edits an item out, rejects
the campaign, run ends failed, decrement is present.

### 2.3 System revocation names no person — **BUILT** (migration 0024)
**Ruled 2026-10-19.**

When confidence falls below the threshold because of a validation-engine flag, no human acted.
`revoked_by` is null, permitted only when `revoked_reason` is a well-formed decay record recording
before and after confidence, the threshold, the cause and the run, and only from the backend role.
A signed-in user cannot forge a decay record.

*Rejected alternatives.* A constant "system" uuid would put a non-person in a column whose purpose
is naming a person. Having engine flags never revoke would leave known-bad memory live until a
human noticed.

*Reportable consequence.* Revocations split into decay-from-flag, decay-from-reviewer-edit, and
revoked-by-person. "How many memory items died by rule versus by review" is a result about the
memory system, not a schema detail.

### 2.4 Confidence is bounded and live items are above the threshold — **BUILT**
**0025 decided 2026-10-20; 0026–0027 decided 2026-10-25.**

`confidence` is `NOT NULL` with no default (0025), constrained to [0, 1] (0026), and a second
constraint requires `revoked OR confidence >= 0.30` (0026). 0027 tightened the decay-record format.

*Reason.* An item written without a confidence could never decay out and was revocable only by a
person — immortal by omission. The table held zero rows, so enforcement was free; once data exists,
a backfill becomes a decision about what the missing values were.

*Ordering note.* The decrement path had to be clamped at 0 **before** the range check was added, or
every decrement at low confidence would have started failing against the constraint.

*Cost, recorded.* 0.30 is hardcoded in the constraint with a test against the config. Raising it
later requires a migration that first revokes what it would strand.

### 2.5 Multi-source evidence takes the maximum — **BUILT**
**Ruled 2026-10-25.**

*Reason — monotonicity.* Adding a source must never lower an item's evidential score. Mean fails
it: corroborating a strong source with a mediocre one makes the item worse. Minimum fails harder:
one weak source destroys an item backed by a good one. Both imply that finding more evidence hurts.
Maximum is the only one of the three that cannot. A 500-case property test asserts monotonicity; a
companion test shows mean and minimum violate it.

*Not modelled.* Corroboration — three independent decent sources arguably beating one — would need
a bonus term with no principled value. Named as future work and explicitly not modelled, including
removal of "corroboration count" from the source-scoring factors, since a source cannot corroborate
itself and corroboration is therefore item-level.

*Schema consequence.* Both the aggregate and the per-source scores are stored, so the aggregation
rule can change later without refetching.

### 2.6 Reuse bands are provisional until E and C exist — **BUILT (the mechanism); bands PROVISIONAL**
**Recorded 2026-10-25. Updated 2026-10-30: E and C now exist; the bands are still PROVISIONAL.**

The M-score is defined over six components (S, P, C, A, F, E). C awaits persona vectors in Phase 6;
E awaits source scores. A missing component is **left out and the remaining weights renormalised**,
and the result names what was left out. No neutral default is substituted anywhere.

*Reason.* A neutral default would produce plausible M values that silently were not the M the
thesis defines.

*Consequence, recorded.* Bands of 0.90 / 0.80 / 0.65 and the write-back gate at M ≥ 0.90 were, from
2026-10-25, applicable only to a four-component M. Nothing banded before E and C exist is comparable
with anything banded after. Band tuning and the ten-campaign approver calibration happen only after
both exist. No figure derived from a four-component M is reported anywhere.

*Update 2026-10-30, what is now true.* The four-component period ended when 0037 went live
(15:45 UTC): E reaches the application through 0031 and C is stored per run and per persona
(`memory_item_personas`) and taken at retrieval by `app/memory/recall.py`, the first caller of
`score()`. **The period produced no banded item**, because `score()` had no caller before that (the
2026-10-26 audit below), so there is no four-component record to set beside a six-component one; what
the rule means now is that any later figure must say which components its M had (`left_out`). **The
bands remain PROVISIONAL: six components existing does not calibrate them.** That is chunk G, against
approver judgement. Until a campaign is approved and written back, live items have no persona rows
and their M still leaves C out.

*Audit, 2026-10-26.* `score()` has no caller outside the tests; no real M has ever been computed
and no recorded figure depends on E. Established by pattern search rather than by reading every
line.

### 2.7 Embeddings are computed by a pinned local model — **BUILT**
**Ruled 2026-10-18.** BAAI/bge-base-en-v1.5, pinned revision, weights hash verified on load. This
superseded an earlier plan to use a hosted embedding endpoint. **[superseded]**

*Reason, in order of weight.* (a) **Reproducibility** — a pinned local model records a revision and
a weights hash that can be checked on every load; a hosted endpoint provides neither, so a
retrieval figure cannot be tied to the exact model that produced it. (b) Quota — embeddings are the
highest-volume call in the system and would starve the strong model tier. (c) The encoder is a
parameter of the method, not the contribution.

*Guards.* Query and document embedding are separate methods, so the query instruction prefix cannot
leak into document vectors. A test asserts the configured dimension equals the vector columns'. The
relevance pool records the encoder revision, so a changed encoder invalidates the frozen pool rather
than quietly producing incomparable numbers.

### 2.8 The temperature is part of the locked call configuration — **BUILT**
**Ruled 2026-10-31 (owner).** The claim classifier and A7's risk read run at **temperature 0**; A6 and the other
generating agents keep the provider's default.

*Reason.* Both are judgement tasks, where variance is noise and not creativity. The evidence: on 2026-10-30 the
same attribute on the same copy was reported with no claim in four identical-input judgements and cited to an
unrelated claim in a fifth (V4, V12), and the same brief-check call returned 48 and 28 output tokens on
near-identical input. No temperature was being set, so the provider's default applied.

*Where it lives, and why there.* In the prompt file itself (`temperature:` in `config/prompts/<id>/<version>.yaml`),
so it is under the same SHA-256 as the text (`versions.lock`). A hash-locked prompt called at another temperature
is a different thing, and "the prompt is locked" would mislead if the temperature sat in code. So a changed
temperature is a new version: `claim_extract@v3` is v2 plus `temperature: 0` (v1 and v2 stay locked and
unchanged, called at the default) and `a7_risk@v2` is v1 plus it. The gateway passes the rendered prompt's
temperature to the provider, and to the repair call of that prompt (the repair of a temperature-0 call is a
temperature-0 call). `llm_calls` already records prompt id and version, so a row names the temperature it was made
at through the version it names; no column was added.

*Provider note (observed 2026-10-31).* LiteLLM warns, on every call, that `temperature`, `top_p` and `top_k` "continue to function for Gemini 3+ (gemini-3.5-flash-lite) but are planned for removal in a future release" and advises moving sampling guidance into the system instructions. The locked temperature works today; it is a dependency on a parameter the provider means to remove, and when it goes the lock has to name whatever replaces it.

*Not shown.* That temperature 0 makes a given model's answers identical across calls (it lowers the variance;
whether the provider is exactly repeatable is a live observation), and whether the Gemini-3-family models degrade
at a temperature other than their default (the provider's documentation warns they can); the first live calls at
temperature 0 are the check.

### 2.9 required_slots was reduced from six to two — **BUILT**
**Ruled 2026-10-31 (owner), before any run.** `retrieval.required_slots` (`config/scoring.yaml`) was reduced from the six
kinds Memory.md section 2 enumerates (`persona`, `competitor`, `insight`, `claim`, `creative`, `sector`) to two:
`insight` and `competitor`.

*Reason, plainly.* Only two kinds can carry a full component set: `insight` and `competitor` items rest on a scored web
page, so they can have `E`; a claim, creative, persona or sector item rests on the brand's own claims or on nothing scored,
so it cannot (V13). Coverage is therefore measured over what memory can CONTAIN, not over what the design enumerates.

*What it prevents.* Left at six, `memory_coverage` was capped at 2 of 6 by construction, so the research-necessity step
would almost never have found memory sufficient, adaptive depth would never have fired, and the Proposed arm's adaptivity
would have contributed nothing: V13 one layer up, a mechanism that cannot operate.

*How it is reported.* Wherever coverage appears it is "k of 2 slots" (`memory_slots`, the step's detail, the run state's
`memory_slots`), never the fraction alone, so a reader cannot take a coverage of 0.5 for a different N. A test ties the
list to the kinds the proposer can write, so a slot added without a way to fill it fails.

*Not shown.* That two slots measure the knowledge a campaign needs: sector and persona knowledge are real inputs to a
campaign, and a coverage over two research slots says nothing about them.

### 2.10 Memory is consumed in Phase 7, and the evaluation waits for it (V14) — **RULED, NOT BUILT**
**Ruled 2026-10-31 (owner).**

*What was found.* Injection of recalled items into the agents was in **no phase** until today. The reuse bands of
`Memory.md` 3.2 (verbatim at 0.90 and above, rephrasable at 0.80 to 0.89, supporting context at 0.65 to 0.79) described
behaviour that nothing performed: `memory_hits` reached no agent, and the only thing the bands fed was `memory_coverage`,
which the placeholder `assess_research_necessity` records and does not act on. The 0.90 and 0.80 bands in particular
named an action with no step to take it.

*Ruling 1 - schedule.* Injection and the research-necessity engine are the same feature under two names ("memory changes
what the run does") and ship together in **Phase 7**, which is restated accordingly: the phase in which memory is consumed
(engine reading coverage and setting depth; injection by band; dynamic agent selection; the daily-quota downgrade, its
original content). `Phases.md` Phase 7.

*Ruling 2 - the memory contrast cannot be claimed before Phase 7.* Until something reads memory the three arms are identical
in memory terms, so ARR, SRR, CR and LR would show no difference for a reason unrelated to the hypothesis. **The evaluation
cannot run until Phase 7 is complete.** Phase 7 moves onto the critical path ahead of Phase 10, recorded in `Phases.md`
(0.1, Phase 7, Phase 10): no evaluation run before memory is consumed.

*Not shown.* That injection will produce a memory effect; this ruling only makes one possible. A two-kind research memory
supports a cost-and-speed story (depth) and, because creative items cannot be written, no copy-reuse story.

### 2.11 What the design describes that no step performs (audit, 2026-10-31)
**Method.** Every behavioural statement in `Memory.md` and `Architecture.md` was read and checked against `Phases.md` (is a
phase or chunk responsible?) and against the code (`backend/app`, by search, not by reading every line). This is the
systematic form of the question that found `source_score` unreadable (0031), memory unwritten (V13) and memory unread (V14).
Statuses: **NOT PERFORMED** = described, built by nothing, scheduled by nothing; **SCHEDULED** = a phase owns it, not built;
**FOLLOWS** = cannot be performed until injection (2.10) exists.

| # | Described in | What it says | Status |
|---|---|---|---|
| 1 | Memory 3.2, 10 | Reuse bands inject verbatim / rephrase / context | Scheduled by 2.10 (Phase 7) |
| 2 | Memory 6.3 | Reuse bump (+0.02, `last_used_at`) for an item used in an approved campaign; "campaign assembly records which retrieved items were used" | **NOT PERFORMED**: `writeback_step` passes `reused=[]` (`orchestrator/adapters.py`); `assemble_campaign` records no used items. FOLLOWS injection |
| 3 | Memory 6.3, 9 | Confidence decrement when validation flags an item or a reviewer edits it out; revocation below 0.30 | **NOT PERFORMED** in a real run: `Correction.flagged` / `.edited_out` exist, nothing stages one. FOLLOWS injection (an item that never reached a campaign cannot be flagged or edited out of one). The "poisoning" defence "confidence decay" is therefore a dead mechanism today |
| 4 | Memory 9 | Echo chamber: cap injected items, force at least one fresh research slot on every run regardless of `R`, track originality over time | **NOT PERFORMED**, unscheduled. (Originality exists only as a human-rated 10% rubric item, `PRD.md`) |
| 5 | Memory 9 | Over-retrieval: hard cap of 12 injected items ranked by `M` | **NOT PERFORMED**, unscheduled. FOLLOWS injection |
| 6 | Memory 3.3, 9 | Verified-claim items exempt from decay "until their source URL fails revalidation"; staleness defence "scheduled revalidation (C4)" | **NOT PERFORMED**: no step revalidates any URL, no phase owns it; and no `claim` item is ever written (V13), so the exemption applies to nothing. Staleness defence is decay alone |
| 7 | Memory 8 | Metrics: reuse depth (items injected), poisoning incidents (wrong facts reaching a campaign via memory), staleness incidents (a human marks a retrieved item outdated) | **NOT PERFORMED**: reuse depth and poisoning FOLLOW injection; no UI or field lets an approver mark an item outdated, unscheduled. Hit rate and coverage are computable now |
| 8 | Memory 7 | Cold start: run 2 hits "brand/sector items", run 3 reaches D0 | Scheduled (Phase 7 engine), but **sector items are never written** (V13), so the run-2 mechanism as written cannot occur; only `insight` and `competitor` items can hit |
| 9 | Memory 7.1; Phases 4 | Seed memory: 3 brands, ~40 curated items each, `provenance='seed'` | **NOT PERFORMED**: the exclusion exists (code and tests); no seed data and no script that creates it exists in `supabase/`, `backend/scripts/`, `config/` or `evaluation/` (searched 2026-10-31; only the `'seed'` provenance value and its exclusion). Phase 4's "Seed data" deliverable and the demo (Phase 11) depend on it |
| 10 | Memory 6.4, 10 | Revocation endpoint and UI control; "a brand manager must be able to revoke from the UI" | **NOT PERFORMED**, unscheduled: revocation exists in the database only; the API has no memory routes |
| 11 | Architecture 10; Memory 10 | `GET /memory/search`: retrieval with `M` breakdown for debug and demo; milestone "memory retrieving with scores - demo-able" | **NOT PERFORMED**, unscheduled (the breakdown exists in `recall.py`; no route returns it) |
| 12 | Architecture 6.1 | `R = wN N + wU U + wF F + wS S + wE E` | Scheduled (Phase 7), **but three inputs have no source**: `N` needs a store of prior requests' embeddings (memory holds facts, not requests), `F` needs a request-type taxonomy, `E` ("claims needing external proof") has no definition of how it is computed. Phase 7 must define them or drop them from the formula |
| 13 | Architecture 7 | "3 to 7 agents" per run | Not achievable on the thesis track: pool is A1, A2, A3, A6, A7, so 3 to 5. `select_agents` is static today (only A2 drops, at D0) |
| 14 | Architecture 4.2 | `route()`: complexity, `is_structural`, reserve floor degrade, escalate on `prior_failures`, Cheap tier for classification etc. | Scheduled (Phase 7). Today every agent hard-codes Standard ("default tier"); the Cheap tier is used by no agent |
| 15 | Architecture 4.1 | Fallback tier (OSS model) for outage or quota exhaustion | **NOT PERFORMED**, unscheduled: `registry.py` states `media` and `fallback` are not routable. An exhausted quota fails the run (C4/C9 gap, Phase 7) |
| 16 | Architecture 4.3 | A rate-limit wait that would push the run past its latency budget ends the step as a partial result | **NOT PERFORMED**, unscheduled: no latency budget exists in code. Phases 5 records a 20-search latency criterion NOT MET |
| 17 | Architecture 8 | Brand-voice gate: cosine distance between copy and the brand voice vector; A1 produces the vector | **NOT PERFORMED**: `product_brand.py` states the vector is not built and the similarity score "not yet scheduled in a chunk". Only the registry half (disclaimers) runs. The gate table still lists the similarity check |
| 18 | Architecture 8 | Risk gate: eight anti-pattern detectors | Known: specified, not implemented, `Rules.md` T5 unmet (3.1). Listed for completeness |
| 19 | Architecture 9 | Organisation `rate_limit_runs_per_hour` (429 at `POST /runs`) and `rate_limit_requests_per_minute` (429 at the gateway) | **NOT PERFORMED**: columns exist; only the monthly cap is enforced (`ledger.py`). `POST /runs` does not exist yet (chunk E) |
| 20 | Architecture 12 | Admin dashboard: D0/D1/D2 distribution, memory hit rate, cap breaches; the depth chart is "the visual proof of the thesis" | Partly scheduled: Phase 7 exit names only "routing distribution in the admin view"; the rest is Phase 12 (deferred). Nothing builds the depth chart on the thesis track |
| 21 | Architecture 9, hardening 2 | Hash chain on `approvals` and `audit_log` "built and verified before calibration begins" | Scheduled in **Phase 10**, but calibration is earlier (Phase 6 chunks E to G). A sequencing gap: confirm whether calibration approvals precede the chain |
| 22 | Architecture 10 | `/campaigns/{id}`, `/runs/{id}/approve|revise`, `POST /runs`, `/experiments/*` | Scheduled (chunk E; Phase 10), not built |
| 23 | Memory 2, 3.1 | Item kinds `persona`, `claim`, `creative`, `sector`; `P` and `A` components | Ruled out by V13 / 2.9 for writing; the `P` / `A` scores still run over the two kinds that exist. Stated, not a gap |

*What this list does not show.* It was built by reading the two documents and searching the code by pattern; it is not a
line-by-line audit of the code, and items marked "to confirm" (9, 21) were not run to ground. Items 2 to 5 and 7 are one gap
seen from five sides: they all wait for injection. Items 4, 6, 10, 11, 15, 16, 17 and 19 were scheduled nowhere.

**Dispositions (owner, 2026-10-31).** Item 4 (and 5, the 12-item cap): SCHEDULED in Phase 7, with injection. Items 2 and 3: in
Phase 7 (used-items record; correction staging). Item 17: specified, not implemented (3.5). Items 6, 10, 11, 15, 16, 19:
named limitations V15 to V20 in section 8, not phased. Item 21: moved to Phase 6 chunk E. Items 12 to 14: the first decision
of Phase 7, each input brought to the owner with options; not resolved on 2026-10-31.

### 2.12 Which latency figure the evaluation reports as LR — **RULED 2026-11-01 (owner proposed, recorded)**
`runs.latency_ms` is one wall figure, and on this machine it is not comparable across arms (V22). A run's active time is
now recorded in parts (migration 0044, `app/evaluation/latency.py`): `model_ms` (summed over `llm_calls`), `search_ms`
(the search provider's calls; about zero under replay, recorded anyway so a live run is comparable), `research_other_ms`
(replayed fetches, extraction, scoring), `encoder_recall_ms` and `encoder_writeback_ms` (local embedding, measured
separately), `waits_ms` (`run_waits`) and a SIGNED `residual_ms` that is never clamped and never folded into a named part.

**LR is reported as model latency plus search latency. Wall-clock latency is reported separately and caveated.** Agreement
with the owner's expectation, with two caveats I add: (1) *model latency is a SUM*, and agents that run at once overlap, so a
sum exceeds the time they took together (the residual goes negative by that overlap, and is shown as such). Baselines B and
Proposed share the same fan-out, so the sum is comparable between them; it is not a critical-path time, and is not called one.
(2) *Under replay `search_ms` is about zero for every arm*, so the search half of LR compares nothing until a run is live; the
recording's own per-query latencies could stand in for it, but that substitution is a separate decision and is not made here.

**Amended 2026-11-01 (owner): the collinearity, and the fix that was already registered.** Under replay `search_ms` is about zero
for every arm, so "model latency plus search latency" collapses to `model_ms`, which is roughly proportional to the number of calls,
which is ARR: two headline metrics measuring one quantity. The snapshot DOES hold what is needed to avoid it: each recorded search
carries `elapsed_s` (the search alone, pacing wait outside the timer) and each request carries `fetch_wall_s` and
`pages_fetched_live` (`app/research/snapshot.py`, `docs/evaluation-plan.md` "LR is reported as counts and derived as time",
registered 2026-10-28 before any data). So **LR is reported in TWO PARTS, as registered**: (1) an **observed** model-time
reduction (`model_ms`, from `llm_calls`); (2) a **derived** web-time reduction = (count difference of searches and of live page
fetches) x (the recorded unit cost: seconds per search, and `fetch_wall_s` per live page at the recorded concurrency), shown with
its inputs and never as an observed latency. Any combined figure is labelled derived. The replay timer `search_ms` stays recorded
(about zero) so a live run is comparable, and is NOT part of LR under replay. *What this does and does not buy:* LR is then a
combination of call counts (ARR) and search and fetch counts (SRR) weighted by unit costs, plus the part of model time that varies
with tokens per call. It is **not independent of ARR and SRR**, and the report states that plainly and prints the correlation of LR
with ARR and with SRR across the requests beside it; **LR is not presented as a separate finding if that correlation is near 1**.
Extraction time (about 0.41 s a page, the same whether 32 or 319 pages were fetched) is local compute, not web time, and is in
neither part (V22).

### 2.13 Budget arithmetic for the evaluation (2026-11-01, before Phase 7)
*What the "60 calls" is.* An **owner-set standing development budget** for Phase 6 chunks D, E and F, spent in the tuning
organisation. It is per project, covers development only, does not refill, and is not a provider limit. **31 of 60 spent** (19 when this was written; the killed-worker run added 12).
*What the provider limits are.* Free tier, per Google Cloud project (so development calls share it): Standard and Cheap are
separate models at **500 requests/day each**, 15/min, 250K tokens/min; Strong is **20/day**, 5/min. The day resets in full at
midnight Pacific (`rpd_resets_at`); there is no rolling refill.

*Calls per run, measured (first real campaign, 2026-11-01), all Standard:* A1, A3, A2, A6, the claim classifier and A7's risk
read = **6**, plus the brief check (ADMISSION) = 7; the brief check is precomputed for the evaluation's 30 requests
(`evaluation-plan.md`), so an evaluation run makes 6. A revision round adds A6, the classifier and A7 = **3**. Research depth
does not change the count (A2 is one call at D1 or D2; the research itself makes no model call). Cheap tier: used by no agent
today (Phase 7 routing may change that).

| Part | Assumption | Standard calls |
|---|---|---|
| Proposed, 30 requests | 6 + expected revisions 1.2 (30% need one round, 10% a second) = 7.2 | 216 |
| Baseline B, 30 requests | same five agents on this track, 7.2 | 216 |
| Baseline A, 30 requests | generate + classify, 2 | 60 |
| Calibration, 10 practice campaigns | generated once (re-scoring uses no model call), 7.2 | 72 |
| Infrastructure re-run allowance | 20% of the 60 pipeline runs, plus A | about 98 |
| **Total** | | **about 660** (480 with no revisions; about 900 if half need a round) |

Strong tier: the plan's own planning figure, 18 to 24 calls over 6 to 8 touching runs, plus the one regulated tuning request.
**Days at the real refill:** Standard **2 days** (660 / 500, and the development calls of those days count too), Strong **2 days**
(24 / 20); RPM and TPM are not binding (660 calls is under an hour at 15/min). **The quota fits before 25 November with room.**
*What does not fit by itself:* the calendar. The experiment cannot start until Phase 7 is built and frozen (2.10); the
Proposed arm needs 3 sequential generate-then-approve rounds per brand (campaign N+1 only after N is decided); 90 campaigns plus
the 10 calibration ones need at least 7 approval sittings of at most 15; and the second scorer and the two raters do not exist
(V21). With 23 days left, Phase 7 frozen by about 12 November leaves a workable window; frozen after about 18 November it does not,
and the fix is then a design change (fewer requests per arm, or a cheaper Baseline B), which has to be taken before Phase 7 is
built, not after. The binding constraints are the schedule and the people, not the model quota.

### 2.16 The approval sittings between the freeze and the rating round (schedule, 2026-11-01)
*Fixed dates (owner):* calibration round 8 November; **Phase 7 freeze 9 November**; main rating round 16 November. *What was
measured and what was not.* One real campaign has been generated (chunk F, 2026-11-01): **297 s of active time (about 5 minutes) per
pipeline run**, replayed research, local encoder. **No human approval time has been measured**: that campaign's decision was
recorded by a script (`seconds_spent` 30, a scripted value). The only documented human figures are `docs/evaluation-plan.md`'s
planning ones: about 6 minutes a campaign typical, the rubric's 10-minute box as the cap, at most 15 campaigns in a sitting; so a sitting of
15 is **about 90 minutes typical, 150 at the cap**, plus breaks.

*Decisions to take.* 30 requests x 3 arms = 90 first decisions. Each approver "revise" adds a second decision on the regenerated
draft. Seven sittings of 15 hold 105 decisions, so **seven suffice only if no more than about 15 of the 60 pipeline-arm campaigns (25%) are
revised by the approver**; at 40% (24) it is eight sittings. That rate is unmeasured (the one real campaign passed first time).

| Day | Machine (worker on mains power, nothing else heavy running) | Sittings | Decisions |
|---|---|---|---|
| Freeze day (9 Nov, evening) | Generate Baseline A (30 runs, about 30 min) and Baseline B (30 runs, about 150 min) for ALL three rounds; Proposed round 1 (10 runs, about 50 min). About 4 hours, unattended | none | none |
| Day 1 | Regenerate any revised round-1 drafts; then Proposed round 2 (about 50 min), once ALL round-1 Proposed campaigns are decided (write-back needs approval) | **S1 + S2** back to back (morning, afternoon): round 1, 30 campaigns (10 brands x 3 arms, shuffled, a brand's N and N+1 never adjacent) | 30 |
| Day 2 | Revised drafts, then Proposed round 3 (about 50 min) | **S3 + S4**: round 2, 30 campaigns | 30 |
| Day 3 | Revised drafts | **S5 + S6**: round 3, 30 campaigns | 30 |
| Day 4 | Final revised drafts; export the logs; verify the hash chain | **S7**: the remaining revised drafts, up to 15 | up to 15 |
| Day 5 | Buffer: a rerun for an infrastructure failure, a sitting that overran | spare | |

*Batching.* Sittings S1+S2, S3+S4 and S5+S6 are pairs that can sit in ONE session each (a morning and an afternoon with a break, never
past the 15-campaign cap per sitting); the three Proposed rounds cannot be generated in advance, because round N+1 waits for the
approval of round N; the Baselines can, and are. Total approver time about 10 to 11 hours typical (105 x 6 minutes), up to about 17 at the cap.
*Calendar if the freeze is 9 November:* Day 1 to Day 4 are 10 to 13 November, the buffer is the 14th, the 15th is free, rating on the 16th. *The latest freeze
that still fits* is the 10th, with the buffer eaten; later than that the main rating round moves or the experiment shrinks.
*Not yet on the calendar and on this critical path:* **the blind presentation harness** (arm labels stripped, constrained
randomisation, logged seed) is not built and is needed for the 8 November calibration and the 16 November round; and the
raters' scope is unsettled: D2 says all 30 requests in every arm (90 campaigns each), the owner's estimate and `PRD.md` say 30
campaigns in all.

### 2.17 Rulings of 1 November 2026 on the rating rubric and the calendar
*(Owner, recorded with the amendment, `docs/rating/rubric-amendment-1.md`.)* (1) The owner authored the missing dimension definitions
and a 0 / 1 / 2 scale (amendment A1); the provenance is a limitation (V24); the amendment is frozen by hash and timestamp before the
8 November sitting. (2) **Raters tick every failed check**, not the first; the first failed gate is derived from the documented order.
(3) **The 8 November sitting is rater agreement, not band calibration**: 10 campaigns from the current system; valid for agreement,
invalid for band calibration, which stays in chunk G after Phase 7. (4) **If the experiment must shrink, cut brands, never campaigns
per brand**: freeze by about 9 November, 10 brands x 3; by about 11 November, 7 brands x 3; later, move the rating round and do not
cut further (campaign 3 is where the memory progression shows). This supersedes the "cut to 20" wording in 2.16.
*Findings raised against the draft anchors, before use* (`rubric-amendment-1.md` section 4): two overlapping anchor pairs (product and
customer relevance), an originality anchor that measures the rater and overlaps at 0/1, undefined "substantive"/"minor" in feasibility
(and a near-duplicate of the approval outcome), a trustworthiness anchor that is a reader's impression and drops the PRD's claim audit,
and **brand consistency, which is UNOBSERVABLE as written: no brand has a stated voice** (the test set states none, `brands.voice_guidelines`
is empty, the copywriter is given none). Ten campaigns also cannot estimate a kappa (one disagreement moves it by 0.1 or more; it is
undefined if a rater scores every campaign the same).

### 2.18 Second set of rulings, 1 November 2026 (rater scope, R as implemented, accounting, supervision)
1. **Rater scope.** Efficiency metrics (ARR, SRR, CR, LR) use all 30 requests across all three arms. **Human quality rating uses a random
   subset of 15 requests, all three arms each: 45 campaigns per rater, blind to arm and interleaved; both raters score the same 45;
   kappa per dimension is computed on those 45.** The subset is drawn at random, the selection frozen before 16 November, the seed
   recorded. *Reason:* a paired quality comparison needs every arm of a chosen request, so the sample shrinks in requests, not in arms, and human
   capacity is the binding constraint. **The quality sample size (15 requests, 45 campaigns per rater) is declared wherever a quality result is reported.**
2. **The 8 November round needs no blinding**: every campaign that day comes from the one current system, so there is no arm to conceal
   and both raters score identical items. It needs the campaigns in readable form and the scoring sheets. The blind presentation harness is
   needed only for 16 November and is built after the Phase 7 freeze.
3. **`R` is described in the write-up AS IMPLEMENTED, not as designed.** Two design changes, with their reasons: (a) **`F` is dropped**: the 30
   requests are all one kind (campaign briefs), so `F` would be near-constant, and the only way to give it values is a request-type map the
   design never specified; (b) **`E` is dropped**: "claims needing external proof" has no definition, every option gives about zero in the evaluation, and the
   research available cannot prove a specific product's claims (V7). `N` is the cosine novelty against the organisation's earlier approved requests (N-a).
   `R = 0.43 N + 0.36 U + 0.21 S`. **FREEZE GATE:** `N` spans about 0.16 to 0.20 between campaigns 2 and 3 against a 0.35 threshold, so `N` does not discriminate within
   an organisation; if `R` gives the same decision for campaigns 1, 2 and 3 the adaptive mechanism is inert and the experiment measures nothing.
   Before Phase 7 freezes the prediction is registered (`docs/phase7-first-decision.md`, "Registered prediction") and tested over the
   10-brand structure with real memory counts; if the three campaigns do not separate, the work stops and `R` is changed before anything is built on it.
4. **Orphaned and duplicate calls: spend versus work.** A crash must not contaminate ARR. The recovery sweep moves `pending` rows orphaned
   by a dead worker to a terminal `orphaned` state (migration 0045). **Orphaned rows count toward SPEND and are excluded from WORK;
   a re-executed attempt counts once in work and every time in spend.** Tested by killing a worker and asserting both tallies.
5. **Supervisor.** Recovery fires only when a worker starts and nothing started a dead one. A supervisor restarts the worker process,
   written down with its assumption: **one worker, held by a lease**; built before the batch.
6. **Revision rate is measured, not assumed.** The seven-sitting estimate holds only if no more than about 15 of 60 pipeline-arm campaigns are revised. The rate is measured on
   the ten campaigns generated for 8 November, then the sitting count is re-derived from the real rate before 9 November. (The measurement is the HUMAN
   approver's: see 2.19.)
7. **Dates** are aligned to the system clock: today is 1 November 2026; records that said 2 November were wrong.
8. **The forced-failure deferral stands**: run X's real `503` plus mocked failure isolation is enough for Phase 7 to build on.
9. **Two proposals, one paragraph each, with a call** (`docs/phase7-first-decision.md`): dynamic agent selection reduces to the single rule "A2 runs
   unless depth is D0", which is a consequence of the engine and not a separate mechanism (ARR then equals the D0 share over five and is collinear with depth): build the rule, record
   "adaptive agent selection" as a limitation. Model routing is a limitation: the Cheap tier is unused, the strong model is close to Standard, so a routing effect is
   unmeasurable here whatever is built.

### 2.20 The freeze-gate check on R FAILED its registered prediction (1 November 2026): Phase 7 build stopped
Registered before the run: campaign 1 -> D2, campaign 2 -> D1, campaign 3 -> D0. Produced (brand B04, real memory, real recall, no model call):
**D2, D1, D1**. Campaigns 2 and 3 have `R` 0.449 and 0.453: the same decision. Cause: `N` does not discriminate (0.21, 0.22), and no recalled item reaches `M >= 0.80` (best 0.715
and 0.771), so `U` is 1 for both even for the same product; the reuse bands are provisional and are calibrated in chunk G, after Phase 7. By the owner's rule ("if the three campaigns
do not separate, say so and stop; change `R` before building on it"), **the Phase 7 build is not started** and the options are in `docs/phase7-first-decision.md`.
Limit of the check: ONE brand has real memory; the other nine need their campaign 1 run (about 63 Standard calls) and have not been, so whether the failure is general is not shown,
though nothing in the mechanism suggests brand B04 is special.

### 2.21 Third set of rulings, 1 November 2026: U redefined, bands frozen, the ceiling, the variance check
Full text and numbers: `docs/phase7-first-decision.md` "Registration 2". (1) **`U` is a saturating function of the QUANTITY of relevant memory**: `n` = recalled items whose semantic match `S` reaches a floor, `U = 1 - min(1, n/12)`.
Reason given in advance: a binary threshold on the best match discards the quantity the hypothesis is about. Floor 0.55 = the 99th percentile of `S` between one brand's items and the other brands' requests (0.531), rounded up to 0.05: a procedure, not an outcome.
Capped linear shape on purpose (no free parameter). The binarisation is ruled out; similarity stays as an input through the floor. (2) **The circularity is broken by freezing, not reordering**: reuse bands (0.90 / 0.80 / 0.65) and depth bands (D0 below 0.20, D2 from 0.32,
the coverage terciles at the reference novelty 0.20) are frozen provisional values; chunk G becomes a RESULT (how closely they matched approver judgement); Phase 10's tuning of the depth thresholds is cancelled; a later change is a new dated registration with both sets
reported. (3) **Development ceiling 250 calls, conditional on billing**: enforced in the ledger (`config/dev_budget.yaml`, `app/llm/dev_budget.py`); no call of any tier until the owner confirms in writing no billing account is linked to `brandverse-ai`. 101 spent, the overrun
(60 to 101) the owner's. (4) **Variance check**: brand consistency is constant by construction on the ten agreement campaigns (no voice attributes exist); product relevance, originality and trustworthiness are likely near-constant; only feasibility is expected to carry kappa. Owner to choose
the remedy before 8 November. **Found while registering:** stage 1 (2.20) scored campaign 3 against campaign 1's memory only, so campaigns 2 and 3 could not have separated; stage 2 must run campaigns 1 and 2 live (about 126 calls).

### 2.22 Where work stopped, 1 November 2026 (end of day, owner's instruction)
**Registered and committed, no stage-2 call made.** The U redefinition (2.21) is registered in `docs/phase7-first-decision.md` "Registration 2", `config/scoring.yaml` `necessity:` and `tests/test_necessity_frozen.py` in the commit that carries this entry,
which precedes every stage-2 run: the next session starts from that registration and must not alter those values without a new dated entry. The development ceiling (250) is enforced in the ledger and `billing_unlinked_confirmed_on` in `config/dev_budget.yaml` is NULL, so every call is
refused until the owner confirms in writing that no billing account is linked to `brandverse-ai`. **Open, in order:** (1) the owner's billing confirmation; (2) the stage-2 scope (nine brands about 126 calls for campaigns 1 and 2 live, or the eight clean brands about 112, or four first); (3) the multi-brand
campaign 1-and-2 runner (not written; `chunk_f_first_campaign` recreates its database); (4) the 8 November remedy for the constant dimensions (2.21 item 4); (5) the Phase 7 build waits on stage 2 separating (at least 6 of 8 clean brands, three bands). Probe scripts that build a gateway directly
(`chunk_b_*`, `v2_probe`, `llm_smoke`, `schema_confirm`, `precompute_brief_checks`, `demo_tier_switch`) now fail at construction without a `dev_budget`: intended, fix per script when one is next needed.

### 2.23 Fourth set of rulings, 3 November 2026: three U variants and their ordering, the test guard, the freeze plan
Full text: `docs/phase7-first-decision.md` section 7a. (1) Stage 2 computes **OLD, FLOOR and COUNT** `U` from one recall; the registered preference ordering is OLD, then FLOOR, then COUNT, and the first that meets 3 of 4 is adopted, so three variants are one test. Predictions registered for each; OLD's marked uncertain (stage 1's artifact). (2) Campaign 2 runs at D1, the less favourable scaffold for separation. (3) Recording of R26 to R30 approved (no model call; AC power only). (4) The 8 November sitting needs no blind harness; it is needed for 16 November only. (5) `pytest-timeout` approved and added. (6) **A hole closed:** importing LiteLLM loaded `.env` into the process, so local test runs held a live provider key and built the real pipeline (CI did not); now `LITELLM_MODE=PRODUCTION`, provider keys removed and the provider call and real encoder refused in every test not marked and named, proven by driving the guard to refuse. (7) **Freeze plan: 11 November, 7 brands; 9 November is upside only.** The billing condition is still unset and no stage-2 call has been made.

### 2.24 Fifth set of rulings, 4 November 2026: billing evidence route, the exposure window left open, the queued recording
(1) **`brandverse-ai` is an UNVERIFIED project name** (the user's statement of 2026-10-17; no console has shown it), and `dattasable1213` cannot see it (gcloud: permission denied, not a false). Nothing was set from that. (2) The fact needed is narrower: whether any billing account exists, i.e. whether a payment method was ever added or a free trial accepted on any Google account. Kundan is asked directly. **No:** `billing_unlinked_confirmed_on` is set with the basis recorded as the OWNER'S STATEMENT, explicitly not a console verification (provenance in `Architecture.md` §4.4, upgraded if a console reading appears). **Yes:** everything stays frozen and the whole spend position is reassessed, including the window below. The date is NOT set yet. (3) **Exposure window 2026-10-17 16:07 to 2026-11-03 17:37 IST (live key in every local test process) is OPEN: neither closed nor a breach.** The post-3-November gate is explained in §4.4; the provider-side count against the ledger's 146 Standard, 4 Cheap and 10 Strong is the only thing that decides it, and is not inferred. (4) **R26 to R30 recording is queued** to start by itself when the stage-2 interim report is written, no stage-2 process is running and the machine is on AC (R01 to R25 are committed; no model call, so no ledger or billing hold). It never starts on battery.

**2.24 addendum (4 November 2026, later).** (5) **Billing CONFIRMED** by gcloud: project `gen-lang-client-0622579315` (`brandverse-ai`) `billingEnabled: false`; `billing_unlinked_confirmed_on` = 2026-11-04. Four closed billing accounts exist (contradicts "never any"; reported). The exposure window stays open. (6) **P17** (every project account is Datta's; scope = credentials the project authenticates with, not the Claude Code login or connectors) with `config/accounts.yaml` and `scripts/account_posture.py`. **Audit closed: no non-Datta project credential**; the Supabase org and the GitHub account's email are owner-ATTESTED, not machine-checked. (7) **Operational risk: the Supabase free plan pauses inactive projects (two of the org's three already paused); `brandverse-ai` must stay live to 25 November.** `scripts/project_state.py` reports active/paused and `live_posture.py` runs it first; resume steps are in `Architecture.md` §11. (8) **The weekly touch is not automated; the pause risk is ACCEPTED** (owner): a laptop scheduler that sleeps, runs on battery and gets closed fails silently, which is worse than a known manual step; daily work with a database session each time makes a seven-day gap unrealistic before 25 November. If there is a stretch of days with no session, run `live_posture.py` once; do not build a scheduler.

**2.24 addendum 2 (4 November 2026): the stage-2 interim CRASHED; no result; staged recovery.** The first live interim (started 09:34 IST, after the billing date was set) failed at 09:53 with `R01: the run never parked for approval`: B01 campaign 1 at D2 sat in **step 6 (research)** from 04:08:48 UTC and never finished, while steps 1 to 5 took 8 s. Localised from the run's Redis event stream, not by a rerun. One Standard call was spent (the brief check in step 3); no other tier; `stage2_interim.json` was NOT written. *Ruled out, no call:* the ledger (refusals RAISE; the only wait is RPM pacing, capped at 90 s and 3 waits; the table holds no `refused` row and `brandverse_stage2` is listed at 0, total 102 of 250); Modern Standby (none between 09:15 and 10:47 IST); the driver and snapshot (a standalone replay of R01 at D2 over the frozen store, no model call, completes in 210 s: 8 searches, 319 pages, 159 findings). *Not ruled out:* ARQ's 600 s job timeout never fired in 15 minutes, which means the event loop itself was frozen (a GIL-holding thread), not an `await` stalled. *What D2 does that D1 does not:* all 8 planned queries instead of the first pass of 4, so about twice the searches, pages and extractions (the R10 run that worked was D1). *Instrumentation added (pushed 005aeff, off unless the worker env sets them):* a named start/done/FAILED log line per pipeline step; `RESEARCH_STEP_TIMEOUT_S` (a `StepTimeout` naming "step 6 research (D2)"); `WORKER_STACK_DUMP_S` (faulthandler all-thread dump, which works through a GIL-held hang); `phase7_stage2.py --probe` (one brand, campaign 1, no report). The stage-2 worker is started with 120 s and 420 s. **Next, in order:** the probe (1 or 2 calls) on AC; only when it completes cleanly, the full interim. R26 to R30 recording stays held until stage 2 is working. The crashed run's leftover ARQ job was removed from Redis db 14 so a new worker cannot pick it up. **Freeze:** 9 to 10 November is gone. The build is 4 to 5 days from the day after the full interim passes: interim passes 5 Nov -> freeze 10 or 11 Nov; 6 Nov -> **11 or 12 Nov**; 7 Nov -> 12 or 13 Nov. **12 November is the last freeze date**; past it the answer is cutting brands further, never moving the 16 November rating. (9) **Standing pattern: anything that rests on a statement and not a reading is recorded as ATTESTED** (dated, with who stated it) beside what is machine-read, as `config/accounts.yaml` does, and is upgraded when a reading is found.

### 2.25 Sixth set of rulings, 4 November 2026: job timeout, visible failure, the recovery gate; and what the second crash showed
**Rulings (owner).** (1) **`worker_job_timeout_s` is 1800 s in production** (it was 600). *Basis, a measurement:* research observed at 205 to 359 s, so 600 s gave under a 2x margin and contention consumed it (the stage-2 probe); 1800 s is about 5x the worst seen. The worker's **unbuffered log is permanent** (`unbuffer_stdio()` in `main()`, not an environment variable): the first crash's diagnosis was lost to block buffering and must not be again. (2) **A timed-out job fails visibly.** The job has its own deadline just inside ARQ's (`worker_job_timeout_s` minus 30 s or 5 percent); reaching it ends the run `failed` with `runs.failure` = `{kind: job_timeout, step, step_name, after_s, infrastructure_failure: process_crash, message}` (migration 0046), emits `run.failed` with the step, and raises `JobTimedOut`. A worker SHUTDOWN is a different cancellation and still leaves the run resumable. A job timeout is `process_crash` in the attempt rule fixed on 2026-10-17; **no fifth kind was added**. Unexpected failures record their exception and step too. Proven against the real ledger by driving a real job into its deadline (`tests/db/test_job_timeout.py`: status `failed`, completed time set, not an orphan, step 7 `run_agents`, the stream's last event `RunFailed(step=7)`), with the shutdown control and the API-role control. (3) **The recovery sweep is serialised** (`app/workers/gate.py`): run jobs share an execution gate, a recovery holds it alone, writer-preferring, waits over a second logged with their length. **THE HAZARD, recorded because it nearly cost us today:** the start-up sweep queues a recovery for every run a dead worker left, the new run's job is queued a moment later, and with `worker_max_jobs` at 2 the two ran TOGETHER; their research fought for the GIL and CPU, the live run's step 3 took 277 s instead of 8 s and ARQ killed it at 600 s. In the evaluation the same collision would not fail a run: it would silently put someone else's work inside a measured run's latency.

**The original crash: EVIDENCE FOUND AFTER THE RULING, NOT CLOSURE.** The ruling was that it stays unexplained, with the fitting mechanism named and the gap stated; that stands as the status of PROOF. What was found since: (a) the Windows Application log has `python.exe` Event 1000 at **09:38:51 IST on 4 November**, faulting module `etree.cp312-win_amd64.pyd` (lxml), exception `0xC0000005` (access violation), three seconds after step 6 of that run started (09:38:48). A segfault writes nothing to a log, leaves the run `queued`, and parks nothing, which is every symptom seen. (b) The second interim (14:01 IST) lost its worker to the same class of crash at **14:10:04** (Event 1000, `python312.dll`, `0xC0000005`), five minutes into research. (c) A plain script with no worker (the R01 D2 replay, run with the fetch concurrency set to 1, which still left the extraction threads free to overlap) **died with `Windows fatal exception: access violation`** (exit 139), faulthandler naming `trafilatura/metadata.py:293 extract_metainfo` while other extraction threads were active. (d) *Mechanism that fits:* trafilatura parses every page with ONE module-level lxml parser (`trafilatura/utils.py:116 HTML_PARSER`); lxml documents that a parser must not be shared between threads; under replay every page is available at once, so eight threads parse against it together. *The fix made:* a process-wide lock on `trafilatura_extract` (every caller, including the agents' evidence reader). *What it shows so far:* two replays with the lock finished (114.6 s and 143.1 s, against 204.8 s and 210.6 s without it) with the same 319 pages and 159 findings, no crash. *The gap, stated:* the 09:38 crash was not captured in a dump, so the link from that event to this mechanism rests on its time, its module and its symptoms; and without the lock about 3 of 8 research executions crashed, so two clean runs with it are weak evidence on their own (a 40 percent chance of two clean runs even if the lock did nothing). It becomes strong only with the eight live campaigns of the interim and the replays that follow completing without a worker death. **Until then: probable cause, fix in place, NOT recorded as solved.** The thread caps stay disabled and are NOT a fix (probe 2 completed without them).

**[WITHDRAWN the same evening: see the CORRECTION paragraph below. The conclusion in this paragraph, that the lock does not prevent the crash, is NOT supported.]** **VALIDATION OF THE LOCK, 4 November 2026 evening (owner asked for eight more locked replays; stopped at the first crash, as ordered). THE LOCK DOES NOT PREVENT THE CRASH.** Locked executions of the R01 D2 replay, no model calls: runs 1 to 4 completed (114.6, 143.1, 151.1, 99.3 s; each 319 pages, 159 findings); **run 5 CRASHED** at 17:51:06 IST, exit code -1073741819 (`0xC0000005`), Windows Event 1000 `python.exe`, faulting module `python312.dll`, **fault offset `0x287890`, the same offset as the worker's crash at 14:10:04**. faulthandler's stack shows exactly ONE thread inside trafilatura (`utils.py:294 repair_faulty_html`, called from `load_html`, called from `bare_extraction`) and every other extraction thread parked at the lock (`extraction.py:131`): **the access violation happened in a single-threaded extraction, in a plain Python regex substitution (`INVALID_XML_CHARS.sub`) in the interpreter's own DLL.** So the shared-parser RACE is not the whole cause, and may not be the cause of this one. *What the crash record now says:* unlocked 3 crashes in 8 executions (lxml `etree` 09:38:51; `python312.dll` `0x287890` 14:10:04; `metadata.py:293` in a script); locked 1 crash in 5 (`python312.dll` `0x287890`). The crash sites differ (lxml, a metadata pass, the regex engine) while the same page set processes cleanly in the other runs, which points at memory corruption or an interpreter or platform fault more than at one bad page or one racing parser; it is NOT established which. Not yet tested: whether `re.sub` over the recorded pages alone crashes (cheap, no model call); a different interpreter build; running extraction in a separate process. *Consequence recorded:* the lock stays (it is harmless and removes a real documented hazard) but is NOT the fix, and the status of the original crash is unchanged: probable class of cause, no confirmed mechanism, no fix proven. The evaluation cannot assume an in-process extraction survives a campaign.

**CORRECTION, same evening (4 November 2026): MOST OF THE CRASHES WERE CAUSED BY THE PROJECT'S OWN DIAGNOSTIC.** The stack dumper added that afternoon (`WORKER_STACK_DUMP_S`, and `faulthandler.dump_traceback_later(60, repeat=True)` in every replay script) reads other threads' Python frames from a watchdog thread without holding the GIL, which CPython documents as unsafe. *Evidence, from the logs:* in every one of the six crashes that happened with the dumper armed (the worker at 14:10:04; the replay script run with fetch concurrency 1; locked run 5 at 17:51:06; the three stock python.org 3.12.10 runs at 18:43 to 18:45) the log shows a traceback line cut off mid-write, `  File ` immediately followed by `Windows fatal exception: access violation`, i.e. the process died while the dumper was writing. The run times also sit on the dump interval: crashes at 63 s, 62 s and 183 s after start against dumps at 60, 120 and 180 s. The repeating fault offset in `python312.dll` (`0x287890` on the uv-managed 3.12.14, `0x27e990` on the python.org 3.12.10) is one fixed spot inside that routine in each build. *What this withdraws:* (a) "the lock does not prevent the crash" (locked run 5 died inside the dumper); (b) "about 3 of 8 unlocked executions crashed" and every crash rate built on it; (c) the suspicion of the platform, the preview OS and the interpreter build, and (d) the case for process isolation as an emergency; it stays a good design (one bad page should not kill a worker) but nothing in this evening's data shows that one does. *What stands:* ONE crash with no dumper armed, the first, in lxml `etree` at 09:38:51, three seconds into a research step, before the lock existed. n = 1: there is no rate. *Removed:* the repeating dumper (setting, worker arming, stage-2 environment) and a test that fails if any code in `app/` or `scripts/` arms it again; `faulthandler.enable()`, which prints a stack only at a fatal signal, stays. *Not yet known, and the next measurement:* whether the unlocked extraction crashes at all without the dumper, and whether the lock matters. An interleaved series of locked and unlocked replays with the dumper gone answers it. Until then the original crash is as it was: PROBABLE class of cause (lxml parser sharing), the fix in place, nothing validated. The supervisor stays as an interim stopgap because the 09:38 crash was real.

*Observation, NOT a claim:* the locked replays were faster than the unlocked ones (114.6, 143.1, 151.1, 99.3 s against 204.8 and 210.6 s). Nothing here explains it; the unlocked and locked runs were on battery at different times, so power state, thermal state and background load are not controlled, and the sample is small. It must not be reported as a benefit of the lock until it is explained.

**What a segfault leaves, and what does not catch it.** The deadline in (2) runs inside the worker, so a worker that dies outright cannot mark its run; that run stays `queued` until the next worker start, when the recovery sweep resumes it (at-least-once). In production the supervisor (`app/workers/supervisor.py`) restarts the worker; the stage-2 runner starts the worker itself and now notices its death within 3 s and stops, naming the exit code. The Windows Application log is the only record of such a death.

**0046 applied to the live project, 4 November 2026.** Dry run first (rolled back, catalog identical after; `supabase/dry-runs/0046_runs_failure.txt`). The first `db push` from the agent session was denied by the auto-mode classifier and was not retried by another route; it was retried on the owner's instruction, reading `DATABASE_URL` from the repository-root `.env` without printing it, and applied. `migration list`: 46 local, 46 remote, no mismatch; `live_posture.py`: 141 of 141 posture checks match, after the account and project-state checks (ACTIVE).

### 2.26 Seventh set of rulings, 5 November 2026: process isolation held, the instrumentation rule, tonight's order
(1) **Process isolation of extraction: HOLD, not built now.** The evidence that made it urgent was produced by the diagnostic (2.25 CORRECTION), and n = 1 is not a rate. **Recorded as a RECOMMENDED PRODUCTION HARDENING with its justification intact:** a native crash in page extraction should never kill a worker, which matters for a product that businesses run. The design, when built: a PERSISTENT pool of extraction processes (not a spawn per page: 319 spawns on Windows cost minutes of pure overhead); on a child death the supervisor recreates the pool itself (`ProcessPoolExecutor` is unusable after a child dies) and retries only the pages that were in flight; **determinism is the constraint**: a retried page must produce the identical finding, asserted at the clean-run figures of 319 pages and 159 findings for R01 at D2. **Rebuilding it is CONDITIONAL on measured data:** if the DUMPER-FREE crash rate across the interim and the first evaluation campaigns (crashes over research steps started, from the supervisor's report and `C:\BrandverseData\logs\stage2-crashes.jsonl`) is non-zero, it is built BEFORE the 90-campaign run; if it is zero, it stays a documented limitation (V25). (2) **New rule `Rules.md` 1.7: instrumentation is code and can be the fault**, citing this investigation as the instance; the `dump_traceback_later` guard test is its enforcement for that case. (3) **The lock/no-lock series is NOT tonight**: the interim is the gate and the night owes R26 to R30 five hours; the extraction lock stays, harmless, unvalidated, and the interleaved series runs tomorrow after the interim has reported. (4) **The python.org 3.12.10 install (`C:\BrandverseData\py312`, `venv312`) stays** as a clean control if anything ever looks interpreter-specific, and is removed once the interim has passed. **Tonight, in order:** the interim with the supervisor and no dumper (started 5 November 18:18 IST, on AC), then R26 to R30 behind it (the watcher was replaced with a 48-hour one because the old one's deadline fell inside the night). The interim reports against the registered predictions and also the measured crash rate, which now decides ruling 1.

### 2.27 The 5 November interim attempts are INVALID: a provider outage, not a result about R
**Attempt 3 (18:18 IST, supervisor, no dumper, extraction lock on): ran to completion and wrote a report that said `passed = False` (0 of 4 brands separating under OLD, FLOOR and COUNT). THAT REPORT IS INVALID AND SAYS NOTHING ABOUT R.** The provider (`gemini-3.5-flash-lite`) answered `503 UNAVAILABLE: This model is currently experiencing high demand` to most agent calls from about 18:24: in the interim's window 35 calls errored and 8 succeeded, and the successes were almost all the short brief-check (`ADMISSION`) calls. The gateway does not retry other provider errors, so A1, A2, A3 and A6 failed in every campaign, the runs still ended `completed` with declared gaps (no copy, no personas), **no memory was written** (the runner's own line read "memory items for the brand now 0" after every campaign, against 9 in probe 2), every campaign 2 and 3 recalled nothing (`n_recalled = 0`), U stayed 1, the bands never moved, and the verdict was computed on empty inputs. The report was renamed `stage2_interim_INVALID_20261105.json` so it cannot be read as a result; the `rest` stage cannot use it. **The registered question was not tested.** *A process failure of mine, recorded:* the recording watcher started R26 to R30 the moment a report existed, valid or not; it was stopped within minutes and rebuilt to wait for a VALID report, and the recording was later started directly (below). *The guard added (17def5a):* a live campaign must end `completed`, with no failed agent, having added memory, or the interim stops as `INVALID RUN, NO REPORT WRITTEN` (exit 3); tested against tonight's exact failure. **Attempt 4 (18:51):** stopped by that guard after the first campaign (A1, A3, A6 failed again, A2 succeeded), costing 5 calls instead of 53. **Budget:** 58 Standard calls in `brandverse_stage2` against the development ceiling of 250 with 101 counted at the ruling, so about 159 used and 91 left; a full valid interim needs about 56 plus brief-checks, so there is room for one valid run and a few aborted starts, not two. **Crash rate, as ruling 1 of 2.26 requires:** attempt 3 started 8 research steps and attempt 4 started 1; **0 worker deaths in 9 research steps**, dumper-free, with the lock on (`stage2-crashes.jsonl` is empty). That is NOT evidence of safety: if the true rate were 1 in 5, 9 clean steps have a 13 percent chance (0.8^9), at 31 percent about 3.5 percent; it makes a high rate unlikely and a low rate untested. Process isolation stays HELD (2.26). **A design problem this exposes, for the owner (it touches a pre-registered rule):** the attempt rule of 2026-10-17 fixes FOUR infrastructure-failure kinds (`rate_limited`, `quota_exhausted`, `network_error`, `process_crash`). A provider 503 "high demand" is none of them, so in the evaluation a provider outage would be recorded as the arm's own agent failures, a result, not an infrastructure failure, and would depress the arm that happened to run during it. The recording of R26 to R30 (needs no model) was started at 19:00 IST on AC.

### 2.28 Eighth set of rulings, 5 November 2026: provider unavailability, interleaved arms, the model and the budget
(1) **The attempt rule is amended: a fifth infrastructure kind, `provider_unavailable`** (any 5xx from the provider). Dated pre-registration amendment in `docs/evaluation-plan.md` ("Amendment 1"), with its reasoning (a provider outage is not a property of the arm; classifying it as an agent failure would penalise whichever arm ran during it) and the explicit statement, **verified read-only against the live project on 2026-11-05, that no evaluation data existed** (0 runs, 0 `llm_calls`, 0 campaigns, 0 approvals; 3 organisations, all `is_test`). Not fitting a rule to results: the rule changes how an ending is classified, decided before any measurement it could change. Code: `ProviderUnavailable`, `ProviderCircuitOpen`, `classify_infrastructure_failure`; migration **0047** adds `llm_calls.outcome = 'provider_unavailable'`. (2) **Bounded 503 retry plus a run-level breaker** (`app/llm/gateway.py`): at most 2 retries per call, backoff 2 s then 4 s plus jitter; each retry is its own admitted row, the failed ones `provider_unavailable`, so they are SPEND (quota, call cap, cost) and never WORK (`llm_attempts.py`, asserted on real rows); **3 consecutive provider-unavailable responses in a run abort it** as an infrastructure failure (`runs.failure.kind = circuit_breaker`), and later calls in that run are refused with no row. All five orchestrator catch sites share one tuple, `ABORTS`, so an abort cannot be swallowed at a site that forgot it (a scan test enforces it). Tonight's 35-errored-calls window is exactly what the breaker stops: the first call of the first campaign would have ended the run. *Stated consequence:* retries use the run's call cap (10 in the stage-2 runs), so a flaky provider can end a run `partial` on the budget before three in a row accumulate; the breaker bounds that. (3) **Arms are interleaved, and the order is registered with its seed**: `evaluation/run_order.yaml`, seed 20261105, method sha256 ordering (no RNG), recomputed by a test; **an adjustment of mine for the owner to confirm:** the randomisation is of the 10 BRAND BLOCKS and of the arms within each request, not of all 30 requests, because a brand's campaigns 1, 2, 3 are sequential by design (campaign 2 and 3 read the memory the earlier ones wrote) and a full shuffle would change what the Proposed arm remembers. **Outage windows are recorded** (`evaluation/outage_windows.yaml`, O-001 from 12:54 UTC on 5 November; derived from the rows from 0047 on by `app/evaluation/outages.py`). (4) **The model is NOT switched.** The standard tier is pre-registered and a switch changes cost, latency and quality baselines together. **Trigger:** if the provider is still failing at the END OF 7 NOVEMBER, bring it back to the owner. If it is ever switched, it happens before any evaluation call and is recorded as a dated change with its reason. (5) **The development budget holds at 250.** 91 remained after the 5 November attempts; a valid interim needs about 56 plus brief-checks, and a guarded abort costs about 5. **Come back to the owner if it drops below 30**: enforced, not remembered: the stage-2 runner refuses to start an attempt under 30 remaining and says so (`budget_floor_refusal`). **Where the night stopped (5 November, 20:30 IST).** Built, tested (1636 non-DB, 718 DB tests green) and pushed (c04eead): the fifth kind, the retries and breaker, 0047 (applied LIVE: migration list 47 of 47, `live_posture` 141 of 141), the registered order and outage record, the budget floor, and the `provider_unavailable` outcome in the accounting. A real bug was caught on the way by an existing test and fixed: LiteLLM gives `APIConnectionError` `status_code` 500, so a plain network failure was being read as an outage and opened the breaker; a transport failure stays `network_error` (test added). **Not yet exercised against a live outage:** the next interim attempt is the first live run of the retry and breaker code. **The interim has not been retried:** it needs AC and the laptop was found UNPLUGGED at 8 percent at 20:28, so neither it nor the recording can run. **The R26 to R30 recording started at 19:00 on AC made 41 attempts and committed none** (the store is still R01 to R25): every attempt aborted on a degraded or timed-out search engine (`query 6 answered degraded` 15 times, `query 3` and `query 1` degraded, and four attempts where all four engines timed out, i.e. the host network path); it was stopped at 20:28 because of the battery. The yahoo and host-network faults of 29 October persist. **The model trigger clock is running:** the provider outage was still failing at 13:28 UTC (18:58 IST) on 5 November; the trigger is the end of 7 November. Open, flagged in the amendment: whether a campaign that ends `completed` with a declared `ProviderUnavailable` gap (one call exhausted its retries while others succeeded in between) should be re-runnable; the rule classifies how an attempt ENDS.

### 2.29 Ninth set of rulings, 5 November 2026 (evening): a provider-unavailable gap is not a result; the shuffle; R26 to R30; the network test
(1) **A provider-unavailable gap is NOT a result.** A run that would end `completed` while carrying a `provider_unavailable` declared gap is INVALIDATED and re-run (infrastructure, not the arm's behaviour; leaving it in would depress whichever arm the outage touched). **The classifier decides, automatically, from the gap kind alone, before any human sees the output**: each agent failure records its infrastructure kind from the exception; a terminal graph node ends the run before the approval step (`failed`, `runs.failure.invalidated`, no memory); nobody reads the copy and then decides. Only `provider_unavailable` invalidates; rate-limit and network gaps are NOT ruled and not assumed (open question for the owner). **Cap: 2 re-runs per campaign; a third failure comes to the owner** (`app/evaluation/reruns.py`). Every invalidation is recorded with its outage window and its re-run (`reruns.invalidations`). Written into Amendment 1 beside the fifth kind, with the same statement that no evaluation data existed (verified read-only on the live project, 2026-11-05). The earlier test that asserted a lone exhausted call was "only a gap" was replaced, because it asserted the opposite of this ruling. (2) **The shuffle adjustment is correct and confirmed:** brand blocks and the arm order within each request are shuffled, never the campaigns within a brand; campaigns 1, 2 and 3 are the memory progression and a full shuffle would destroy what the experiment measures. Recorded next to the seed (`evaluation/run_order.yaml`, `schedule.py`, the plan) with "do not fix this". (3) **R26 to R30: ONE more attempt, then drop it.** 41 attempts committed nothing on 5 November against the same degraded-engine fault as 29 October. One clean session on AC, behind a VALID interim, when the host network is healthy. **If it fails: the 10-brand option is recorded as UNAVAILABLE, the criterion stays on the brands B01 to B08 support, and no more nights are spent on it.** (4) **The `APIConnectionError`-is-500 test is kept** (network errors read as outages would have aborted and re-run healthy runs for the wrong reason, silently). **Interim, same evening:** attempt 5 (21:01 IST, on AC) was the first live run of the retry and breaker code and behaved as designed: the provider answered 503 to A1, A3 and A2 within eight seconds, the third consecutive response opened the breaker, and the run ended `failed` (`circuit_breaker`, fifth kind) at a cost of about 5 calls; the old waiter then sat 15 minutes for a campaign that could not park, which the new `wait_parked_or_ended` ends at once. The interim is retried automatically every 8 minutes while the provider is down (stops on a valid report, a budget refusal or battery).

### 2.30 STOPPED, 5 November 2026 22:25 IST: the development budget can no longer complete a valid interim
**The decisive fact.** 48 of the 250 development calls remain. **A full valid interim needs about 64** (8 live campaigns at about 7 calls, plus a brief check each), so **a valid interim can no longer be completed within the budget even if the provider recovered completely.** The owner's floor of 30 is not yet reached (48 > 30), but the structural shortfall makes further attempts pointless without a decision, so I stopped rather than spend the headroom proving it. Nothing is running.

**Why "attempt freely at about 5 calls" did not hold.** Measured cost per attempt tonight: attempt 6 **5**, attempt 7 **9**, attempt 8 **10**, attempt 9 **14**. The estimate assumed the breaker would end a run after three calls. It does not bound the cost that way: **each 503 retry is its own admitted row (spend, by the ruling), and a SUCCESS resets the consecutive streak**, so an intermittent provider produces far more rows than a dead one before three failures land in a row. Attempt 9 cost the most precisely because it was going well: B01's campaign 1 completed and wrote **8 memory items** (the first live campaign to write memory since probe 2), then campaign 2 hit the breaker. The budget was spent as the ruling intended, on spend-not-work rows; the estimate of the rate was mine and it was wrong.

**Attempts 5 to 9, all INVALID, no report written** (each exit 3, `stage2_interim.json` never created; the surviving file is still `stage2_interim_INVALID_20261105.json` from the 18:18 attempt). Causes: the circuit breaker fired on 4 runs (`ServiceUnavailableError`), and attempt 6 was caught by the campaign-integrity guard with all four agents failed. **The registered question has still not been tested: there is no count for OLD, FLOOR or COUNT, nothing met the 3-of-4 criterion, and the ordering adopts nothing.**

**What the retry and breaker code did in anger (its first live outing, as designed):** 503s classified `provider_unavailable`, retried twice on new rows, the third consecutive answer aborting the run with `runs.failure.kind = circuit_breaker` and the fifth kind; `wait_parked_or_ended` ended each dead attempt in seconds instead of the old 15-minute wait. The invalidation path of ruling 1 was NOT exercised live (no run reached `completed` carrying an outage gap; the breaker got there first), only in tests.

**Crash rate, for ruling 1 of 2.26: 0 worker deaths in 6 research steps tonight, 15 dumper-free research steps in all (9 on 4 November), `stage2-crashes.jsonl` empty.** Still consistent with a low rate and not evidence of safety: 15 clean steps at a true rate of 1 in 5 has probability 0.8^15 = 3.5 percent, so a rate that high is now unlikely; a low one remains untested. Process isolation stays HELD.

**Cold start is NOT contaminated by the repeated attempts.** Each attempt seeds a FRESH organisation per brand, so the 23 memory items now in the scratch database sit in 3 organisations that no later attempt reads; campaign 1 of a new attempt still recalls nothing. Verified by query, not assumed.

**Two things I stopped deliberately, both reversible.** (a) The automatic retry loop, after attempt 9: it was spending about 10 calls every 8 minutes against a provider that had been answering 503 for four hours. (b) The recording watcher: R26 to R30 has ONE attempt left (ruling 3), an automatic trigger cannot tell whether the host network is healthy, and last night's 41 aborts were exactly that fault, so the last attempt is started deliberately after a valid interim and an engine probe, not by a timer.

**Also fixed tonight:** the worker log is now one file per attempt. A retried interim overwrote the previous attempt's worker log, which destroyed the evidence of why it failed (I lost attempts 5 to 8's provider exceptions that way, and had to read the cause from the database instead).

**The owner's decisions, none of which I can make:** (1) raise the ceiling, or (2) cut the interim's scope (fewer brands per attempt, so a valid run fits in 48 calls, which changes what the 3-of-4 criterion is computed over and is therefore a registration change), or (3) wait for the provider and accept that the freeze slips. **Freeze:** 12 November was the last date that holds the 16 November rating; a valid interim on 6 November still gives 11 or 12 November, but only if the budget question is settled first. The model-switch trigger (end of 7 November) is unchanged and the provider was still failing at 16:40 UTC.

### 2.31 Tenth set of rulings, 5 November 2026 (late): ceiling 500, scope NOT cut, the cost bounded under intermittency
(1) **The development ceiling is RAISED to 500**, same enforcement in the ledger, with a **report tripwire at 400** (`config/dev_budget.yaml report_to_owner_at_calls`, `DevBudget.report_due`; the stage-2 runner refuses to start an attempt past it and says to report). The reasoning is recorded in the config file beside the figure, because a ceiling that moves for convenience is not a ceiling: the 250 existed to bound spend while the BILLING CONDITION was unconfirmed; billing is now confirmed unlinked by gcloud (2026-11-04); the standard tier is free-tier at 500 requests a day, so 500 development calls is inside one day's free quota and buys no paid usage; and the 250 was consumed by a **provider outage**, not by the work. **Development scope only: the evaluation gets its own budget and this file is not it.** This supersedes the floor of 30 remaining from 2.28. (2) **Option 2 is REFUSED: the interim's scope is NOT cut.** Recorded with the owner's reason, which is the general principle: changing what the 3-of-4 criterion is computed over, under schedule pressure, because of a provider outage, is fitting the registration to an accident. (3) **The cost is now bounded under an INTERMITTENT provider, not only a dead one.** My breaker bounded a dead provider; a success resets the consecutive streak, so a flaky provider slipped past it, which is what the 5, 9, 10 and 14 calls of attempts 6 to 9 were. **"About 5 calls per attempt" was wrong, and the reason is now written where the code is:** the estimate assumed three calls would end a run, but each 503 retry is its own admitted row and any success restarts the streak. Two bounds added: a **per-run TOTAL cap of 6 provider-unavailable responses, which a success does NOT reset** (`llm_unavailable_total_cap`; it aborts the run whatever the streak is, and the gateway refuses to be built with a total below the streak), and a **per-attempt cap of 75 calls** in the stage-2 runner (`ATTEMPT_CALL_CAP`), checked BEFORE each campaign and allowing for that campaign's own worst case (`PER_RUN_CALL_CAP` 10), so **an attempt cannot exceed 75: the worst case is quotable, not estimated**. A clean interim is about 64 calls, so campaigns stop starting at 65 spent and a clean run never reaches it. (4) **Attempt the interim tonight, retrying every 20 minutes while the provider is intermittent**; each attempt is also the health check, because a dead provider now aborts cheaply. (5) **Attempt 9 is recorded as PROGRESS: B01's campaign 1 completed live and wrote 8 memory items** — the write-back path working on real models outside a probe for the first time since probe 2 (chunk B's runs wrote none; V13 was the earlier failure of exactly this path). The interim's remaining work is the other seven campaigns, not the mechanism.

**Where this stands at 22:52 IST.** Rulings 1 and 3 are in and tested (1660 non-DB and 717 DB tests green; one DB test needed fixing because it builds a `DevBudget` directly and the new field is required). Remaining under the raised ceiling: **298**. The attempt loop is armed but **the laptop was unplugged again at about 22:45** (`PowerLineStatus=Offline`, 92 percent), so no attempt has started: the AC-only rule stands for timed jobs, and the risk it guards against is Modern Standby suspending a job mid-run, which is what ended recording session 3. The loop now WAITS for AC (up to three hours) instead of giving up, so plugging in starts it; it reads `PowerLineStatus`, not `Win32_Battery.BatteryStatus`, which reads 1 both for "discharging" and at high charge and had already produced one wrong answer tonight.

### 2.32 THE STAGE-2 INTERIM IS VALID AND IT FAILED THE REGISTERED CRITERION (6 November 2026)
**The result.** First valid interim: `stage2_interim.json`, 2026-11-06 06:25 UTC, registration commit `80ddb80`, 4 brands, 8 live campaigns, 65 calls, **0 worker crashes in 8 research steps**. **Separating brands (three distinct bands across campaigns 1, 2, 3): OLD 1 of 4, FLOOR 0 of 4, COUNT 2 of 4. The criterion was 3 of 4. NO VARIANT MET IT, so the registered ordering adopts NOTHING and `--stage rest` refuses.** Per brand: OLD `D2/D1/D0` for B01 and `D2/D1/D1` for B02, B03, B05; FLOOR `D2/D2/D2` for all four; COUNT `D2/D1/D0` for B01 and B03, `D2/D1/D1` for B02, `D2/D2/D1` for B05.

**Against the registered predictions (7a), which is the point of having made them.**
- **OLD (registered `D2/D1/D1`, marked uncertain): HELD.** Observed exactly `D2/D1/D1` in three brands, and `D2/D1/D0` in B01. The registration's stated reason for it also held: items start at confidence 0.60, so no recalled item reaches M >= 0.80 and old U stays 1.00 — true in every campaign except B01's third (U 0.50). A correct prediction of a mechanism that mostly cannot separate.
- **FLOOR (registered `D2`, `D1` alt `D2`, `D0` alt `D1`): FAILED.** Observed `D2/D2/D2` in all four brands; **campaign 3 at D2 was not among the registered options**. The cause is measured, not guessed: the 0.55 similarity floor passes almost nothing. `n_floor` was 0, 0, 2 (B01), 0, 0, 0 (B02), 0, 1, 3 (B03), 0, 1, 1 (B05) while `n_count` for the same campaigns was 0, 6, 12 / 0, 5, 6 / 0, 6, 12 / 0, 1, 7. This is exactly the risk recorded on 3 November as "the first sign that the 0.55 floor may be set where few real items pass" (section 7, found while writing the runner). It is now confirmed on real recall: **the floor, not the quantity of memory, is what holds U near 1.**
- **COUNT (registered `D2`, `D1` alt `D2` if campaign 1 wrote <= 4 items, `D0` alt `D1` if the two campaigns wrote <= 8 between them): HELD IN ALL FOUR BRANDS**, modal or by its own stated alt. B01 and B03 modal `D2/D1/D0`. B02 `D1` at campaign 3 — its brand held 6 items, under the stated 8, so the alt applies. B05 `D2` at campaign 2 — its campaign 1 wrote **1** item, under the stated 4 — and `D1` at campaign 3 with 7 items, under 8: both alts apply. **COUNT's model of the world was right; its own registered alt branches are what stop it separating.**

**The pattern the data shows, and it is not about U.** The two brands that separated are exactly the two that accumulated 13 memory items (B01 6 then 13, B03 6 then 13). The two that did not are the two that stalled (B02 5 then 6, B05 **1** then 7). **The binding constraint is how much memory a campaign writes, not which definition of U is used.** A brand that writes ~6 items per campaign separates; one that writes 1 to 5 does not, because `n_count` stays under the 8 of COUNT's own alt and R never falls into D0.

**What this does NOT show.** It does not show that R cannot discriminate; it shows that under THIS write-back volume, with the bands frozen on 1 November, 3 of 4 brands do not produce three distinct depths. Nothing is adopted and nothing is tuned: the bands, the floor and the criterion stay exactly as registered until the owner rules.

**Consequence.** The Phase 7 build does not start on a failed interim (the same position as stage 1, 2.20, which is now the SECOND registered failure of R's discriminability). **Freeze: a date can no longer be computed from the registered path.** If the owner rules on R today and a re-registered re-run passes today, the build is 4 to 5 days from tomorrow, giving 11 or 12 November — 12 November being the last date that holds the 16 November rating. Any slip past today puts it past that limit, where ruling 4 of 2.17 applies (cut brands, never campaigns).

**Budget: 367 of 500 spent, 133 remaining, and only 33 before the 400 report tripwire.** This valid attempt cost 65 calls, inside the 125 cap. **A further full interim attempt (about 65) would cross the tripwire**, so the next one needs the owner's word first, which is what the tripwire is for.

**Three things found on the way (6 November).**
1. **The overnight failures were NOT the provider.** Attempts 10 and 11 hit genuine 503s; **attempts 12 to 21 all failed on `getaddrinfo failed`** — this laptop could not resolve `generativelanguage.googleapis.com`. Ten attempts and about 50 calls were spent on a local DNS fault that the logs reported as "the agents failed", and from 23:49 IST onward there was no information about the provider at all. The `APIConnectionError`-is-500 fix of 5 November is what kept these classified as `network_error` instead of being read as an outage.
2. **The provider had recovered.** `scripts/provider_probe.py` (new, one call per tier, retries off) answered on both 500/day flash-lite models, including the current standard tier `gemini-3.5-flash-lite`. **Neither of the owner's two cases applied, so NO MODEL SWITCH was made.** The probe refuses to spend a call at all unless it can resolve and connect to the API host first, precisely so a local fault can never be reported as a provider verdict.
3. **A per-run cap that contradicted the design it was measuring.** A campaign costs 7 or 8 calls with no revision round and **3 more per round** (A6 once, A7 twice — the claim classifier and the risk reader both log as A7), so with `max_revision_rounds` 2 its worst case is 13; the runner capped a run at 10 and B02's campaign 2 ended `partial` at exactly that point in attempt 22. Raised to 15, and the attempt cap from 75 to 125 on the measured costs: my 75 assumed 7 calls a campaign, the same error as "about 5 calls per attempt". Changing `max_revision_rounds` instead was rejected: the rounds are part of what the evaluation measures.

### 2.33 Write-back volume diagnosed from the completed runs (ZERO model calls); the criterion is NOT lowered; budget 700 (6 November 2026)
**Owner's rulings.** (1) No R amendment yet; diagnose write-back volume from the runs already completed, before spending anything. (2) **The 3-of-4 criterion is NOT lowered to match COUNT's 2.** That would be fitting, the one move that would make the registration worthless. (3) Budget **700, report at 600**, with the per-attempt accounting in the same message. (4) State plainly that the registrations were sound and the criterion's precondition was not met, which is the difference between a mechanism that does not work and one whose input was thin.

**The funnel, reconciled per campaign from each run's checkpoint, its `memory` record and the rows it inserted (`provenance = run:<id>`). Scripts only read the stage-2 database and the replayed snapshot; no model call.**

| campaign | findings | A2 kept after quote check | removed by the copy screen | facts | proposed = staged = inserted | refused | deduplicated |
|---|---|---|---|---|---|---|---|
| B01 C1 | 159 | 6 | 0 | 6 | 6 | 0 | 0 |
| B01 C2 | 109 | 8 | 1 | 7 | 7 | 0 | 0 |
| B02 C1 | 198 | 7 | 2 | 5 | 5 | 0 | 0 |
| B02 C2 | 99 | 2 | 1 | **1** | 1 | 0 | 0 |
| B03 C1 | 188 | 6 | 0 | 6 | 6 | 0 | 0 |
| B03 C2 | 101 | 10 | 3 | 7 | 7 | 0 | 0 |
| B05 C1 | 153 | 7 | **6** | **1** | 1 | 0 | 0 |
| B05 C2 | 104 | 7 | 1 | 6 | 6 | 0 | 0 |
| total | | 53 | 14 | 39 | 39 | 0 | 0 |

**What this shows.** (a) **Everything after A2 is lossless: 39 proposed, 39 staged, 39 inserted; zero skipped by the proposer, zero refused by write-back, zero deduplicated.** The proposer, `stage_writeback` and the insert path are not where volume is lost, and no refusal rule fired. (b) **All of the variation is inside A2** (one model call per campaign, then two deterministic screens). (c) **It does not follow the research volume**: A2 reads exactly 6 pages in every campaign (its own cap), whatever the 99 to 198 findings; B02 C2 had 99 findings and yielded 1 fact, B01 C2 had 109 and yielded 7. (d) **Two screens act on A2's output, and only the second removes anything:** the quote check dropped 2 items in total (B03 C1 and B05 C1, 1 each; the counts are unverifiable items plus whitespace); the **copy-wording screen** (`safe_derived`: no 5 consecutive words copied from a page the model read, no markup or URL, 200 characters) removed **14 of the 53 verified items (26 percent)**.

**Why B05's campaign 1 wrote 1 item and B01's wrote 6.** B05 C1 (Ustara Grooming): the model produced 7 verified items and the copy screen removed 6 of them, every one for a verbatim 5-word run from a page ("lubricating strips with aloe or", "heads that follow jaw contours", "the risk of irritation bumps and razor", ...). I rebuilt the exact six page excerpts A2 was shown and confirmed each rejection against the page text: **all 14 are genuine verbatim 5-word runs; none is a markup, length or URL rejection; none misfired.** B01 C1 had no such overlap and kept all 6. A correction to the framing in the ruling: the campaign that "reached 13" was not B02's; the cumulative figures were B01 6 then 13, B03 6 then 13, B02 5 then 6, B05 1 then 7, and B02's thin step was its campaign 2 (+1), where **the model itself offered only 2 items** from its six pages (the screen then removed 1 of them). So the two 1-item campaigns have DIFFERENT causes: B05 C1 is the screen (6 of 7 removed), B02 C2 is thin model output (2 offered).

**Defect or not defect: my classification, and where it does not fit the two cases offered.** *No defect was found.* The proposer, write-back and insert path lose nothing; the copy screen does exactly what it is written to do on every one of its 14 rejections. But the cause is **neither "refusals firing wrongly" nor "that brand's research yielded little"**: the research yielded plenty (99 to 198 findings). It is (i) **the output of a single stochastic model call per campaign, which offered between about 2 and 10 items** and which I cannot characterise from one sample per campaign, and (ii) **a security screen (threat V10) whose threshold, `COPIED_RUN_WORDS = 5`, the code itself calls "an untuned judgement"**, and which removed 26 percent of verified items and 6 of 14 in one campaign. Whether that threshold is right is a design and security decision, not a repair, and I have not touched it. I therefore do not claim to be in the owner's first case (a defect to fix) and I do not claim the second (thin research) either; the closest honest statement is that **the system's yield per campaign is variable and was thin in two of eight, for two different reasons, and R's discriminability depends on accumulated memory.**

**The observation that bears on a precondition (NOT registered; the owner rules).** At campaign 3 the brands that separated had `n_count` **12 and 12** (B01, B03; U under COUNT 0.00); the three that did not had 6, 6 and 7. At campaign 2, `n_count` of 5 or 6 gave D1, and 1 gave D2 (B05). COUNT's frozen definition is `U = 1 - min(1, n/12)`, so **12 is the formula's own saturation point from 1 November, not a number read off these results**, which matters for the next point.

**Registrations were sound; the criterion's precondition was not met.** Said plainly, because it is the distinction that matters. **COUNT's registered prediction held in all four brands** (modal in B01 and B03; its own stated alternatives in B02, where the two campaigns wrote 5 + 1 = 6, at most 8, giving D1, and in B05, where campaign 1 wrote 1, at most 4, giving D2, and the two campaigns wrote 1 + 6 = 7, at most 8, giving D1). **OLD's registered prediction held in three brands** (B02, B03, B05 at `D2/D1/D1`) and missed in B01 (`D2/D1/D0`). FLOOR's did not hold, for the measured reason in 2.32. The mechanism behaved as registered, including in predicting its own failure to separate when write-back is thin. **What the criterion lacked was a condition on the input: "3 of 4 brands separate" presumes each brand accumulates memory, and two of four did not.** That is a mechanism whose input was thin, not a mechanism that does not work; but two separating brands out of four is a small sample and does not show that it works at scale.

**What would be a legitimate amendment, and the guard against fitting.** If the owner rules for a precondition, the criterion is restated in terms of brands that meet it, as a dated amendment with this reasoning, before any re-run. The risk is exactly the one the owner named: choosing a precondition after seeing which brands separated. The mitigations available are that the threshold would be a quantity fixed in advance of the brand's outcome (accumulated recalled items at the time of the campaign, with 12 taken from the frozen formula), and that it is defined by what the brand's memory held, not by whether R then separated. **It is not made here.** Also stated for the owner: **the thin yield is a property of the system, not only of the test brands**, so in the evaluation the Proposed arm will meet thin brands too; a precondition would therefore change what the research question claims, and should be reported as a limitation, not hidden.

**Not done, and why.** No re-run (no defect to repair, and no amendment ruled). No change to the 0.55 floor, the bands, the copy-screen threshold, `max_revision_rounds` or the criterion. The remaining unknown, how much A2's yield varies run to run, cannot be answered from one call per campaign and would cost model calls.

**Budget: ceiling 700, report at 600** (`config/dev_budget.yaml`, enforced in the ledger; the report tripwire is read by the stage-2 runner). Same grounds as the raise to 500: billing confirmed unlinked, the standard tier is free-tier at 500 requests a day, development scope only. **Per-attempt accounting of the 367 spent** (it reconciles exactly): 101 counted at the ruling; **9** on the first crash and two probes (useful); **105 on PROVIDER 503s** (attempt 3 the 18:18 run 44, attempt 4 5, attempts 5 to 9 43, attempts 10 and 11 13); **52 on the LOCAL DNS FAULT** (attempts 12 to 21: this laptop could not resolve the API host); **35 on attempt 22, my own per-run cap of 10 contradicting `max_revision_rounds` 2**; **65 on the one valid attempt (23)**. So of the 266 spent in the stage-2 database, **192 were infrastructure or an error of mine and 65 were the work** (9 diagnostic). After the raise: 333 remain, 233 before the tripwire at 600.

### 2.34 The 6-page limit of A2: not registered; justification, DRAFT page budget and predictions, items examined and left alone (6 November 2026; ZERO model calls; AWAITING THE OWNER'S RULING)
**Owner's rulings.** (1) Establish first whether the 6-page limit is registered anywhere. (2) Write the justification before the data and independent of it. (3) Either way pre-register a page budget per depth band and a prediction per brand per variant before any call. (4) Do not touch the 5-word screen. (5) Do not tune A2's prompt toward a minimum item count. (6) Budget 700 accepted; record the headline. **No interim attempt runs until the owner has ruled on (1).** None has.

**1. Is it registered? No. It is an unexamined implementation constant.** Searched `Architecture.md`, `Memory.md`, `Phases.md`, `PRD.md`, `Design.md`, `Rules.md`, `docs/*.md`, `config/*.yaml` and the prompt `a2_market@v1`. `MAX_PAGES = 6` (with `MAX_PAGE_CHARS = 2500`) entered `backend/app/agents/market.py` in commit `a7d7116` (30 October 2026). The only prose is a build-status sentence in `Phases.md` (~line 550, "reads up to six accepted pages") written in the same commit, which describes the code and registers nothing; the Architecture row for A2 states no page count; the prompt states output caps ("up to six" conventions, "up to eight" messages) but no page count. **So changing it is a REPAIR of the same class as the per-run call cap, not an amendment.** Repairing is not fitting only if the reason is fixed first (item 2). The other constants on the same path (`MAX_PAGE_CHARS`, `MIN_QUOTE_CHARS = 12`, `MAX_FOR_COPY = 8`, `COPIED_RUN_WORDS = 5`) are likewise unregistered; they are not touched here.

**2. The justification, written before the data and independent of it.** *D2 performs roughly twice the searches and fetches of D1, and A2 reads 6 pages at both, so the registered depth parameter has no path to the quantity it is supposed to influence (memory write-back volume).* Measured afterwards, with no model call, by replaying the frozen searches of R01 to R25: **queries are exactly 2.00x (D2 against D1); distinct pages fetched are 1.657x** (mean 315.9 against 190.7; range 1.52 to 1.80); validated against the measured R01 at D2 (319) and R02 at D1 (186). A2 read exactly 6 pages in all 8 campaigns, at D1 and at D2. The 'roughly twice' is therefore 1.66 to 2.0 depending on whether queries or pages are counted.

**3. DRAFT pre-registration (not in force until the owner rules; committed before any call).** Page budget per band, derived from what each band fetches: **D0 = 0** (D0 does no research; nothing to read); **D1 = 6** (the status-quo anchor: the only band with a measured behaviour); **D2 = round(6 x 1.657) = 10** (pages fetched; 12 if queries were the basis). *Stated weakness:* the anchor is a choice. D1's 6 is 3 percent of the ~191 pages D1 fetches, and nothing says 6 is right in absolute terms; scaling only D2 leaves D1 where it was. Alternatives for the owner: keep D1=6 and D2=10 (as drafted), or anchor both on fetched pages in a stated proportion.

*Predictions, from the frozen scoring functions (`score_campaign`) and ONE stated model: per-page yield y = 39 facts / 48 pages = 0.81, brand-independent; novelty per campaign from the valid interim; only campaign 1 (D2, cold start) changes.* Campaign 1 would write ~8 items at 10 pages (5 at 6). Campaign 2 (D1, 6 pages) writes ~5. **COUNT separates a brand only if campaign 1 writes 4 to 7 items AND the cumulative memory reaches 8 to 9** (the window, computed for each brand: B01, B03, B05 need 8, B02 needs 9). Under the draft budget:

| variant | B01 | B02 | B03 | B05 | brands separating |
|---|---|---|---|---|---|
| OLD | D2/D1/D1 | D2/D1/D1 | D2/D1/D1 | D2/D1/D1 | 0/4 |
| FLOOR | D2/D2/D2 | D2/D2/D2 | D2/D2/D2 | D2/D2/D2 | 0/4 |
| COUNT | D2/D0/D0 | D2/D0/D0 | D2/D0/D0 | D2/D0/D0 | 0/4 |

(Control: the same model at the current 6/6 predicts COUNT 4/4 and the interim observed 2/4, so the model is crude; the per-brand observed C1/C2 yields were 6/6, 5/1, 6/6 and 1/6.)

**Said plainly: this registration predicts the change will NOT help, and COUNT may do worse than at 6 pages.** The reason is structural: at 10 pages campaign 1 writes ~8 items, which already pushes campaign 2 (a D1 campaign) past the D1 band under COUNT (U = 1 - 8/12 = 0.33, R below 0.20, D0). The 2-to-10 spread in A2's output (one stochastic call per campaign) dominates any plausible budget effect, and the prediction carries that uncertainty: any single brand can land anywhere. The outcome is not chosen; if the owner rules for a different anchor, the table is recomputed and re-committed before the first call.

**4. The 5-word copy screen was examined and deliberately left alone.** It removed 14 of 53 verified items; every one was verified as a genuine verbatim run of five words (2.33). It is a security control (threat V10); loosening it to improve an experimental result is not a trade made at any ratio. `COPIED_RUN_WORDS` and `safe_derived` are unchanged.

**5. A2's prompt is not tuned toward a minimum item count.** The 2-to-10 spread in items offered per campaign is a property of the system. It belongs in the limitations section of the write-up: the evaluation's Proposed arm will meet thin brands too.

**6. Headline for the write-up.** Of the 266 calls spent in the stage-2 database, **192 went to infrastructure or arithmetic errors, not to the experiment** (105 provider 503s, 52 local DNS fault, 35 my own per-run cap contradicting `max_revision_rounds`); 65 were the one valid attempt and 9 diagnostic. Budget ceiling 700, report at 600; 367 spent, 333 remain, 233 before the tripwire; a full attempt is about 65 calls.

### 2.35 Five rulings, 6 November 2026: the build proceeds with the 3-of-4 criterion NOT MET; the page budget is repaired and REGISTERED; the interim is re-run once as evidence, gating nothing
**1. The Phase 7 build proceeds, deliberately, and the gate is NOT MET.** Stage 2's registered criterion (at least 3 of 4 brands show three distinct bands under a variant) was **not met**: OLD 1/4, FLOOR 0/4, COUNT 2/4 (2.32). **The decision to proceed is deliberate, and is the owner's.** The reasoning: the gate's purpose was to prevent building a mechanism that cannot fire; R fires in 2 of 4 brands with every registered prediction holding (COUNT's in 4 of 4, OLD's in 3 of 4, FLOOR's failure explained, 2.33); and the criterion was registered **before write-back variance had been measured**, so it had **low power against a 2-to-10 items-per-campaign spread**. **The criterion's author is the owner (Kundan).** It is not lowered and not restated; it was not met. As for chunk G, **the gate becomes a REPORTED RESULT, not a hidden failure: R's realised discrimination across the full 30-campaign evaluation is a PRIMARY reported finding, whatever it shows, including that R rarely discriminates.** Superseded: the statements in 2.32 and the memory note that the Phase 7 build waits on the interim.

**2. The page limit is repaired on its own merits (2.34 item 1: unregistered, a repair of the class of the call cap).** `PAGES_BY_DEPTH = {D0: 0, D1: 6, D2: 10}` in `backend/app/agents/market.py`; a state without a depth is D1. **D1 is deliberately unchanged**: nothing in the construct argument justifies raising it and it would be a second change riding on the first. D2 = round(6 x 1.657), the measured pages-fetched ratio (2.34 item 2). Tests pin all four cases. The 2.34 draft table below is **IN FORCE as the registered prediction**, committed before the re-run's first call.

**REGISTERED PREDICTION for the re-run (depths of campaigns 1/2/3 per brand B01, B02, B03, B05, identical for all four, from the stated yield model of 2.34): OLD D2/D1/D1 (0/4 separate); FLOOR D2/D2/D2 (0/4); COUNT D2/D0/D0 (0/4).** Stated again: this predicts the repair will NOT make the criterion pass and that COUNT may do worse than at 6 pages; per-brand outcomes are expected to scatter around it because A2 is one stochastic call per campaign. A result of COUNT at 3 or 4 of 4 would refute the yield model, not confirm the repair. **Reported against this table either way; it gates nothing.** Per-run cap 15 and attempt cap 125 unchanged; 333 calls remain before the ceiling.

**3. The re-run's purpose is evidentiary.** It tests the registered prediction and validates the repaired budget end to end (A2 reading 10 pages at D2 in a real run) before Phase 7 builds on it. It runs alongside the build, not before it.

**4. Unregistered constants on the same path: a KNOWN CLASS, not fixed opportunistically.** `MAX_PAGE_CHARS = 2500`, `MIN_QUOTE_CHARS = 12`, `COPIED_RUN_WORDS = 5` (the screen, a security control), `MAX_FOR_COPY = 8`, the `_RawMarket` list caps (conventions 8, messages 10, whitespace 6) and the prompt's differing output caps ("up to six", "up to eight"). Each is an implementation constant that the registered documents do not state. Listed for registration or deliberate retention; the screen and the prompt stay untouched.

**5. Build order.** The Phase 7 build is the critical path; the freeze target 11-12 November stands (12 November is the last date holding the 16 November rating). The plan is in the report of this date.

### 2.36 Phase 7 build rulings, 6 November 2026 (owner): the fresh slot is option (a); injection reaches A6 (A1 a stretch); component 5 dropped; an evaluation budget scope; the cap audit
**1. THE FORCED FRESH SLOT: option (a). D0 performs exactly ONE fresh query, and D0 is redefined as "one fresh query", not "zero searches"** (supersedes `Architecture.md` 6.1's "reuse memory only, zero searches" and `Phases.md`'s "D0 skips step 6"; those documents are amended with this entry). *Reasoning:* the over-retrieval and echo-chamber risk (`Memory.md` section 9) is greatest when the system is most memory-reliant, so the freshness floor must hold at D0 above all; the alternative (a D0 with no search) would have improved a headline metric (SRR) by removing a safety property. *Consequence, stated openly:* **SRR is lower than it would be with a search-free D0**: a D0 run saves 3 of 4 queries (or 7 of 8), not all. **SRR is reported PRIMARILY WITH the mandatory fresh query included.** A decomposition showing the floor's contribution separately may be given, labelled as a decomposition and never as an alternative headline.

**2. Scope of injection (component 3): A6 in; A1 only as a stretch once everything else is green; A3 out, as a recorded limitation (memory's two kinds, insight and competitor, do not inform personas); A7 reads only the finished copy and gets none.** **The evaluation's quality result must therefore be read in this light: memory informs the FINAL COPY (A6, and A1 if built), not the personas and not the strategy beyond A1.** This is a limitation of the experiment, stated beside every quality figure.

**3. Component 5 (the used-items record and reuse bump) is dropped**, on the reasoning in `docs/phase7-first-decision.md`: the bump is +0.02 confidence at M >= 0.90, and over three campaigns per brand it cannot change an outcome. The designated casualty, decided now and not on the 11th. Components 1, 3 and 4 are the hypothesis and are not droppable.

**4. THE EVALUATION BUDGET SCOPE is built before the freeze.** Found in the audit: **no such scope existed.** A gateway on the real provider refuses to be built without a ledger carrying the development budget, and that ledger refuses a database not listed in `config/dev_budget.yaml` and counts the rest against the development ceiling of 700. As built, the evaluation's roughly 660 calls would have been refused, or would have drawn on the development calls remaining; it would have stopped the evaluation on its first day. Built as a separately registered scope, sized for about 660 plus re-runs, with a test that no development-ceiling state can refuse an evaluation call.

**5. The cap audit** (component 6): every cap on the evaluation path states the registered parameter it bounds and carries a test asserting it exceeds that parameter's worst case, as `PER_RUN_CALL_CAP >= 13` does. Scope: `PER_RUN_CALL_CAP`, `ATTEMPT_CALL_CAP`, the page budgets, the worker and research timeouts, `max_revision_rounds`, the circuit-breaker and total caps, the 12-item injection cap, the strong tier's 20 a day.

**6. Schedule.** The planning date is the **evening of 10 November**; the build is aimed at the 9th. The last six days lost roughly half of every plan to power, DNS and the provider, none of it coding. The 11-12 November freeze holds.

### 2.37 Attempt 24 against the registered prediction (6 November 2026): criterion NOT met, as predicted; the repaired page budget works end to end; two COUNT cells missed
**Result.** Valid, all 12 campaigns, 74 development calls (the estimate was about 65). OLD 0/4, FLOOR 0/4, COUNT 1/4 separating; **needed 3, not met, nothing adopted.** Gates nothing (2.35). Spent: 441 of 700 (259 remain, 159 before the tripwire at 600).

| variant | registered prediction (all four brands) | observed |
|---|---|---|
| OLD | D2/D1/D1, 0/4 | **D2/D1/D1 in all four. HELD 4 of 4.** |
| FLOOR | D2/D2/D2, 0/4 | **D2/D2/D2 in all four. HELD 4 of 4.** |
| COUNT | D2/D0/D0, 0/4 | B01 D2/D0/D0 **held**; B03 D2/D0/D0 **held**; **B02 D2/D1/D0 missed (it separates)**; **B05 D2/D2/D0 missed**. 2 of 4 cells held; separating brands 1/4 (B02), prediction 0/4. |

**Reading it.** (1) **The registered prediction that the repair would not make the criterion pass held**: 1 of 4 against 3 needed. (2) **The repair did what it was for.** A2 read exactly 10 pages in every D2 campaign and 6 in every D1 (checked from the run checkpoints, no call); the items recalled at campaign 2 rose from 6, 5, 6, 1 (mean 4.5, attempt 23) to **9, 6, 12, 3 (mean 7.5)**, +3.0 against the +3 the yield model predicted for 10 pages. n = 4 per attempt, so this is a direction and not an estimate. (3) **The yield model is right on the mean and wrong per brand**: it said every brand writes about 8 and the spread is **3 to 12**. B01 and B03 overshot (9 and 12 items, so campaign 2, a D1 slot, falls to D0 under COUNT: the predicted overshoot); B02 (6) and B05 (3) did not. (4) The two missed cells are the spread, not a new mechanism: B05's D2 campaign had A2 offer about 5 items of which 1 was removed by the copy screen, 3 reached memory. The model was told per-brand outcomes would scatter and they did. (5) **COUNT's separation is a property of write-back volume landing in a window (campaign 1 writes 4 to 7 items, cumulative 8 to 9)**, which one stochastic model call per campaign hits about one time in four; **R's realised discrimination is reported across the 30-campaign evaluation as a primary finding (2.35).**

**Examined and left alone, as ruled:** the 5-word screen (it removed items again, each a genuine run), A2's prompt, the other unregistered constants (2.35 item 4).

### 2.38 Rulings on the fresh slot's reach, the documents, the order, and what attempt 24 established (6 November 2026, owner)
**1. A2 stays OFF at D0; D0's page budget stays 0; component 2 is not revised. The freshness floor is PARTIAL.** Stated before anyone reads the results: at D0 the one fresh query surfaces new SOURCES to A3 as evidence titles, but **writes no new memory**, because extraction (A2) is skipped to preserve the agent-selection reduction. *Why the experiment is not exposed:* over a brand's three-campaign sequence, campaigns 1 and 2 run at D2 and D1 and both write memory, so the echo chamber the floor guards against (memory feeding on memory with no new material) cannot form inside the experiment. *Sustained production use WOULD be exposed* (a brand that runs at D0 campaign after campaign writes nothing new), and that is on the **production-hardening list beside process isolation**. **A correction to the 6 November fresh-slot reasoning (2.36 item 1): it overstated what option (a) delivers. It buys a fresh SOURCE, not fresh MEMORY.** The SRR treatment stands unchanged: the query costs a search and is counted.

**2. Documents.** `fake_run.py`, `Architecture.md`, `Memory.md` and `Phases.md` are amended so that none still says D0 is zero searches (inconsistent documentation is how the next unexamined constant gets written).

**3. Remaining order:** cap audit, then the confirmation run (about 65 calls; 259 remain, so one repeat fits), then the documents. A1 injection only if everything else is green.

**4. What attempt 24 established (2.37), stated as strongly as the evidence allows.** OLD's registered prediction held in **4 of 4** brands and FLOOR's in **4 of 4**, exactly as written: **the system's behaviour is predictable; it is the discrimination that is thin.** A model that predicts its own failure correctly is evidence that the model is sound. **COUNT fell from 2 of 4 to 1 of 4 after the page repair, while recall at campaign 2 rose from a mean of 4.5 to 7.5: the repair moved the quantity, and moved brands OUT of COUNT's window rather than into it** (B01 and B03, at 9 and 12 items, overshot the window in which campaign 2 stays a D1 slot). It is the clearest evidence yet that **the binding constraint is the per-campaign yield variance (3 to 12 items from one stochastic call), not any definition of U.**

### 2.39 The cap audit (6 November 2026; ZERO model calls; `tests/test_cap_audit.py`, `app/core/call_budget.py`)
Each cap on the evaluation path, the registered parameter it bounds, that parameter's worst case, and the result. A cap is wrong when nothing says what it bounds; each row below is now a test.

| cap | value | bounds | worst case (derivation) | result |
|---|---|---|---|---|
| production calls per run (`MAX_LLM_CALLS_PER_RUN`, Architecture 12) | 20 | calls in one run | 13 logical (brief check 1, A1/A2/A3 3, 3 per draft x 3 drafts) + 6 provider-unavailable retry rows = **19** | covers it, **margin 1**. A structured repair (7 in 340 calls measured) is not in the 19; a second repair at the very worst case is refused and the run ends partial, declared. *Finding, not changed (registered figure).* |
| the same, as a start-up check | production only | the figure above | `worst_case_calls(max_revision_rounds, total cap)` | **NEW:** a production configuration below it does not start (e.g. `max_revision_rounds` raised to 5 with a cap of 20 is refused) |
| stage-2 `PER_RUN_CALL_CAP` | 15 | one stage-2 campaign | 13 logical | covers it plus a repair; **does not cover the six retries**, so an outage can reach the cap before the provider-unavailable abort (that run is invalidated on its gap either way). Not on the evaluation path. |
| stage-2 `ATTEMPT_CALL_CAP` | 125 | one interim attempt | 8 live campaigns x 15 = 120 | covers it |
| A2 pages (`PAGES_BY_DEPTH`) | 0 / 6 / 10 | pages read per depth | D2 >= round(D1 x 1.657, the measured fetch ratio) | covers it; rises with depth |
| worker job timeout | 1800 s | wall time of one run | research at its timeout 900 s + 11 sequential calls x 60 s = **1560 s** (no repair or retry); longest measured run 516 s, longest call 277 s | covers it. A repair or retry on EVERY call passes 1800 s: that is the timeout doing its job (a visible failure). |
| research step timeout | **off by default** | the research step | the evaluation worker sets 900 s (as stage 2 does) | *Finding:* the default is None, so the only bound on a stuck research step is the 1800 s job timeout. **ENFORCED (owner, 6 November):** a production or evaluation (`EVALUATION_RUN=true`) worker with real agents refuses to start unless `RESEARCH_STEP_TIMEOUT_S` is set between 60 and 1140 s (job timeout minus the sequential calls after research); also in `docs/runbook.md`, but the refusal is the control. Stage-2 `PER_RUN_CALL_CAP` raised 15 -> 20 and `ATTEMPT_CALL_CAP` 125 -> 160 (8 x 20) so a campaign cannot go partial on retry rows during the confirmation run. |
| injection cap | 12 | injected items per run | the frozen `item_cap`; U saturates at the same 12 | equal by construction |
| evaluation call budget | 660 + 300 = 960 | evaluation calls | plan 660 + one re-run in three | **built (2.36 item 4)**; disjoint from the development ceiling by test |
| strong tier | 20 a day | strong calls in a day | the plan expects 18 to 24 in the regulated runs | **the worst case is ABOVE the cap**: a scheduling requirement (spread those runs over two days by hand), asserted by a test so it is not silent; a T4 call is never downgraded |

### 2.40 A3's invalid-output history (zero calls), the sequence rule, and the A3 output cap (6 November 2026)
**1. A3's history, from every call log on this machine (scratch databases, zero calls).** Answered requests (an `ok` or `invalid_output` row; the repair rows are counted apart):

| agent | answered | invalid output | needed the repair | failed after repair |
|---|---|---|---|---|
| A1 | 41 | 0 | 0 | 0 |
| A2 | 28 | 0 | 0 | 0 |
| **A3** | 37 | **8 (22 percent)** | 9 repair calls (6 ok) | **1** |
| A6 | 50 | 0 | 0 | 0 |
| A7 | 95 | 0 | 0 | 0 |

**Not a flake: materially higher than every other agent (0 of 214 for the other five).** *Cause, from the gateway's own log of the validation errors:* of 7 logged invalid outputs, **4 were JSON cut off** ("EOF while parsing a string at column 4570 / 4634 / 4763", and one empty) and **2 were lists over the schema's maximum of 3** ("objections: at most 3 items, not 5 / not 7"). The cut-offs are the 1024-token output cap: three personas with three lists each reach it, and the repair is asked the same question under the same cap and is cut at the same place. **Fixed:** A3's `max_output_tokens` 1024 -> 2048 (A2, the longest answer, has had 2048 throughout), pinned by a test; no prompt change. **CORRECTED the same day (see 2.41): the claim that was here, that `a3_personas@v1` does not tell the model the three-item limits, was WRONG.** I read a truncated line. The prompt says "up to three short phrases" for jobs, objections and channels, "between one and three personas" and "under 240 characters" for the summary: every limit the schema enforces. The 2 over-list outputs (5 and 7 objections) are the model disobeying a stated instruction, not a prompt/schema disagreement, so there is nothing for a v2 to state. *Expected effect of the cap alone:* it removes the cut-off cause (4 of 7 logged); it cannot be shown to remove all, and the rate must be re-read after the next real runs.

**2. The sequence rule** is recorded as `docs/evaluation-plan.md` Amendment 2 (the full reasoning is there) and built in `LLMGateway.structured` and `classify_infrastructure_failure`. The A3 sequence of 6 November (503, invalid, invalid repair) is now classified `provider_unavailable`, would invalidate the run, and counts against the 2-re-run cap like any infrastructure failure. The single-repair rule is untouched.

### 2.41 A3 prompt v2 is NOT created (my premise was wrong); a successful answer after a retry is kept and FLAGGED (6 November 2026)
**1. A3 prompt v2: approved by the owner on the premise that the prompt and the schema disagree, "the model is asked for something and then rejected for producing it", with the hard constraint that the diff may only state limits the schema already enforces. Checked against the file before writing it: the premise is false.** `a3_personas@v1` already states every limit the schema enforces: one to three personas; the summary "under 240 characters"; "jobs_to_be_done, up to three short phrases"; "objections, up to three short phrases"; "channels, up to three channels". A v2 constrained to state only those limits has an EMPTY diff, so none was created; a v2 with any wording would break the owner's own constraint, and a released prompt version is not changed for nothing. The correction to 2.40 above stands as the record. *What remains true:* 4 of the 7 logged invalid outputs were truncation (fixed by the 2048 cap); 2 were the model listing 5 and 7 objections against an explicit "up to three". Whether that survives the cap is **measured, not assumed: A3's invalid-output rate is re-read after the next real runs against the 22 percent baseline (8 of 37 answered), and reported.** If over-listing persists, the options are to bound it in code (truncate to the first three, a normalisation the schema could do) or a prompt change, and each is a ruling; neither is made here.

**2. A successful answer after a provider-unavailable retry is KEPT (owner ruling).** Discarding successes would cost enormous re-runs to remove a speculative effect (Amendment 2 governs sequences that END in failure). The deliberate choice has two controls so the honesty does not need the re-runs: (a) **the campaign is FLAGGED as containing a provider retry**, derived from `llm_calls` (a run with any `provider_unavailable` row; `app/evaluation/provider_retries.py`, tested pure and against a database), so those campaigns can be identified at any time; (b) **the analysis runs a sensitivity check, every headline figure with and without the flagged campaigns** (`sensitivity()`), and a result that moves materially between the two is reported as such.

**3. Amendment 2 accepted as built.** The practice stands: every amendment is accompanied by a read-only confirmation that no evaluation data exists (done for Amendment 2 on 2026-11-06: 0 runs, 0 `llm_calls`, 0 campaigns, 0 approvals).

### 2.42 The end-to-end confirmation run (6 November 2026): Phase 7 works end to end; two findings the owner must read before the freeze
**Run.** `--confirm`: B01, R01 / R02 / R03 all live on real models, depth by the necessity engine (`depth_policy: necessity`), memory injected into A6. Valid, 23 calls (8 + 7 + 8), no report, gates nothing. Development spend 473 of 700 (227 remain, 127 before the tripwire). The first attempt (9 calls) ended on A3's invalid output after a 503 (2.40); this is the second and only other.

| campaign | depth chosen | R | memory items after | injected into A6 | in A6's prompt |
|---|---|---|---|---|---|
| R01 (cold start) | **D2** | 0.786 | 7 | 0 | n/a (shows "(none)") |
| R02 | **D2** | 0.398 | 14 | 7: all `support` (M 0.669 to 0.733) | 7 of 7 |
| R03 | **D2** | 0.415 | 25 | 12 (of 14 considered: 1 below the floor, 1 over the cap): all `support` (M 0.660 to 0.743) | 12 of 12 |

**What is established.** The engine runs at step 5 under the necessity policy and records R and the depth; A2 read 10 pages at D2 and ran (not D0); memory is recalled, banded, capped at 12, recorded in the campaign's `strategy`, and **reaches A6**: for each campaign the prompt A6 builds from the checkpointed run state (real prompt library, the agent's own `memory_block`, zero calls) contains every injected note under its band label, and the strategy record equals the run's injection record. `a6_copy@v3` was used by all calls.

**FINDING 1: the built engine never lowered the depth.** R was 0.398 and 0.415 at campaigns 2 and 3, above the D2 threshold (0.32), so all three campaigns ran at **D2**. The engine implements the registered Registration 2 definition (U from the count of recalled items at the semantic floor 0.55, "FLOOR"), and with the memory a brand actually has, almost nothing passes 0.55 (n 0 to 1), so U stays near 1 and R stays at D2. This is the stage-2 FLOOR result (D2/D2/D2 in 4 of 4, 2.37) appearing in a live run, not a new fault. **Consequence, stated plainly: on these brands the Proposed arm researches at the depth Baseline B does, so the search-reduction metrics (SRR and the depth effects behind CR) will read near zero by construction of the engine as registered.** COUNT, which counts every recalled item, did lower the depth in some brands (2.37), but it is not the engine that is built and was not adopted (the gate failed, 2.32). Switching is a registered-definition change and the owner's to rule on; none is made here.

**FINDING 2: only the weakest injection band ever fires.** Every injected item was in the `support` band (0.65 to 0.79: "[background]", context only, not to be echoed); the highest M was 0.743. No item reached `adapt` (0.80) or `reuse` (0.90), so the "[use as written]" and "[adapt]" paths are exercised by tests and not by real data. The memory effect on the copy will therefore be that of background context, by what the match scores of this system actually produce. A limitation to read beside every quality figure, in addition to 2.36 item 2.

**A3 after the 2048 cap (n = 3 requests, no conclusion):** campaign 1 had one provider-unavailable retry and then answered (a FLAGGED campaign under 2.41); campaign 3 had one invalid output that the single repair fixed.

### 2.43 Rulings on the confirmation findings (6 November 2026, owner): the engine counts COUNT; the reuse bands are untouched; A1 injection dropped; A3 waits
**1. The engine is switched from FLOOR to COUNT, as a dated amendment** (`docs/phase7-first-decision.md` section 7b; `necessity.u_source: count`). **Justification, as ruled: FLOOR makes the necessity engine inert, returning D2 for every campaign observed in two independent runs (2.37, 2.42), and a mechanism that cannot vary cannot be evaluated. The switch is made to obtain a testable experiment, not a better result.** The construct argument against the 0.55 floor predates any stage-2 data (2.21, 1 November; 2.23 and section 7a item 1, 3 November). Read-only on 2026-11-06: no evaluation data exists (0 runs, `llm_calls`, campaigns, approvals, memory items). **Predictions registered before any evaluation run** (section 7b): campaign 1 D2 everywhere; campaign 2 D1 (alt D2 or D0) for non-regulated brands and D2 for B07; campaign 3 D0 for non-regulated brands and D1 for B07; **9 of 10 brands show a depth reduction by campaign 3; SRR about 44 percent with the fresh query counted (range 35 to 55)**. The switch and its reasoning appear in the write-up. Built: `u_source` in the config and the engine, pinned by `test_necessity_frozen`, FLOOR kept selectable and tested.

**2. The reuse bands are NOT touched.** The same test as for FLOOR, reaching the opposite answer: nothing reached 0.80 and the highest M observed was 0.743 (a finding about M's distribution, reported beside every quality figure), but unlike FLOOR it does not make the experiment untestable, since memory still reaches A6 and can still affect the copy. Re-basing thresholds onto observed values is fitting, and is refused.

**3. A1 injection is DROPPED, not a stretch.** With only the background band firing it adds little and the schedule is worth more. **Recorded as a limitation beside the A6-only scope: memory informs the final copy (A6) and nothing upstream of it: not A1's positioning, not A3's personas.**

**4. A3 over-listing: wait for real data** against the 22 percent baseline (8 of 37). No change.

### 2.44 REDUCTION is not SEPARATION: the gate over-specified the hypothesis, and the two figures are reported together, always (6 November 2026, owner)
**THE DISTINCTION, in the owner's words and with the owner's error owned.** The 3-of-4 criterion required three DISTINCT depth bands per brand (campaign 1 > 2 > 3). **The hypothesis requires only that work falls as memory accumulates, which is REDUCTION, not three-level discrimination.** **The owner over-specified the operationalisation when the criterion was set on 1 November (2.21), and that over-specification is why four days went into a gate the thesis did not need in that form.** The gate was applied as registered and the result stands (not met); what changes is how stage 2 is *reported*.

**REPORT BOTH, ALWAYS TOGETHER, NEVER EITHER ALONE:**
- **Depth REDUCTION by campaign 3** (campaign 3 runs shallower than campaign 1): the property the hypothesis requires.
- **Three-band SEPARATION** (D2 > D1 > D0): the stricter property the registered gate tested and did not meet.

Neither figure may appear in the write-up without the other beside it: reduction alone would overstate; separation alone would understate and would misrepresent what the hypothesis claims. **Enforced in code, not left to discipline:** `InterimVerdict` carries `reducing` beside `separating`, `InterimVerdict.both()` is the one way the runner prints them, and a test recomputes both from the raw depths of the two committed reports.

**Measured (recomputed from `evaluation/stage2/stage2_interim_20261106.json` and `..._attempt24.json`; 4 brands each):**

| | attempt 23 (6 pages) reduction / separation | attempt 24 (10 pages) reduction / separation |
|---|---|---|
| OLD | 4 of 4 / 1 of 4 | 4 of 4 / 0 of 4 |
| FLOOR | **0 of 4 / 0 of 4** | **0 of 4 / 0 of 4** |
| **COUNT** | **4 of 4 / 2 of 4** | **4 of 4 / 1 of 4** |

**Attribution, which the figures need beside them (my addition; the owner rules whether it stays).** A reduction is not automatically a reduction *caused by memory*. At campaign 1 there is no prior request, so N = 1.0 and R is high whatever memory holds; from campaign 2 N falls to about 0.15. Under COUNT that fall alone leaves R about 0.41 with an empty memory, which is D2 under the frozen bands (R >= 0.32), so **COUNT's reduction below D2 is produced by U, that is by accumulated memory.** OLD's 4 of 4 is **not**: its U stays 1.00 throughout (items start at confidence 0.60 and never fill a slot), and its different bands (D1 from 0.35) make a constant R of about 0.42 read D1. OLD's reduction therefore comes from novelty collapsing against its own bands, not from memory. FLOOR's 0 of 4 is the inert engine of 2.43. So the reduction figure that supports the hypothesis is COUNT's: 4 of 4 in both attempts, with separation 2 of 4 and 1 of 4 beside it.

**The 4 November citation, as ruled:** the owner dated the construct argument to 3 to 4 November; there is no 4 November entry on it, and none is cited (2.43). Words are not put in the record that are not there.

### 2.45 The attribution is promoted to a registered analysis: memory versus novelty (6 November 2026, owner)
**1. The attribution of 2.44 stays and is promoted from a note to a REGISTERED ANALYSIS** (`docs/evaluation-plan.md`, "Registered analysis: how much of the depth reduction is MEMORY, and how much is NOVELTY"), pre-registered before any evaluation run with its expected direction and a read-only confirmation that no evaluation data exists (0 runs, `llm_calls`, campaigns, approvals, memory items).

**2. Built, zero calls:** `app/evaluation/necessity_decomposition.py` (per campaign: R, N's, U's and S's contributions, the depth chosen, and the counterfactual depth with U held at 1.00; the share of depth reduction attributable to accumulated memory; reads the engine's record from `campaigns.strategy -> 'necessity'`, which `build_strategy` now stores), `scripts/phase7_decomposition.py`, tests including the committed stage-2 reports and a database test of the reader. **The control the three arms do not provide:** Baseline B has no memory and runs at a fixed depth, so nothing in the arms separates "memory reduced the work" from "the request was less novel the second time".

**3. Expected direction, stated in advance, with its honest reason.** Under COUNT and the frozen bands the memory share is expected to be about 100 percent, **by the algebra of the bands**: at U = 1.00 R is at least 0.25 / 0.70 = 0.357, above 0.32, so novelty alone can never take the engine below D2. The analysis therefore checks that the logged inputs obey it (an exception would be an anomaly) and says what drove the ENGINE'S depth, not whether the research needed was smaller.

**3a. (Owner, same day.) The 100 percent follows from the FROZEN BANDS, not from the data**: under COUNT's weights with U at 1.00, R floors at 0.357 against a D2 threshold of 0.32, so novelty alone cannot produce a reduction by construction; **the property is contingent on those bands, and if they change the attribution is RECOMPUTED, not carried over** (a test pins it). **What the decomposition establishes is narrower than it looks: the registered configuration makes accumulated memory NECESSARY for depth reduction, and the analysis verifies the implementation obeys that. OLD is the contrast case where novelty alone suffices; that pairing is the evidence, and the 100 percent on its own is algebra.** *Whether reduced depth costs quality is the raters' question on 16 November; the two results are read as the pair they are.* Written into the registration beside the structural note.

**4. OLD is the negative control and its column is reported beside COUNT's, with the attribution attached to both** (reduction 4 of 4 in both attempts; attribution: COUNT's reduction 100 percent memory, 9 and 13 levels; OLD's 11 percent and 0 percent memory, the rest novelty against its own bands). **OLD reducing for the wrong reason is itself evidence that the measure can move without memory.** It is not dropped because its reduction is uninteresting.

**5. Remaining before the freeze, in order (owner):** the COUNT live run, the `Architecture.md` and `Memory.md` injection descriptions, then the freeze tag. R26 to R30's last attempt and the python.org removal are low priority and may slip past the freeze.

### 2.46 The COUNT live run (6 November 2026): B01 ran D2 / D1 / D0 end to end; a runner rule that called a correct D0 outcome invalid is fixed
**Run.** `--confirm` with the COUNT engine: B01, R01 / R02 / R03 live on real models, depth chosen by the engine, memory injected into A6. 24 calls (7 + 10 + 7). Development spend 497 of 700 (203 remain, 103 before the tripwire). **n = 1 brand: it shows the mechanism working end to end, not a rate.**

| campaign | depth chosen | R | N | U | memory recalled | injected into A6 | in A6's prompt |
|---|---|---|---|---|---|---|---|
| R01 | **D2** | 0.786 | 1.000 | 1.00 | 0 | 0 | n/a |
| R02 | **D1** | 0.279 | 0.166 | 0.58 | 5 | 4, all `support` (M 0.656 to 0.692); 1 below the floor | 4 of 4 |
| R03 | **D0** | 0.058 | 0.135 | 0.00 | 14 | 12, all `support` (M 0.670 to 0.779); 2 over the cap | 12 of 12 |

**Established.** (1) **Three distinct depth bands in a live run** (D2 > D1 > D0), the behaviour the amendment of 2.43 was made to allow. (2) **D0 is what was registered:** R03 made exactly **one search**, ran agents A1, A3, A6, A7 and not A2, and wrote no memory (14 items before and after). (3) **The cap works:** 14 recalled, 12 injected, 2 over the cap, recorded. (4) The engine's record (`strategy.necessity`) is stored with each campaign. (5) **The decomposition on this live data** (zero calls): the two reduced campaigns removed 1 and 2 levels, and the counterfactual depth with U held at 1.00 was D2 in both (R 0.428 and 0.415), so 3 of 3 levels are memory and 0 novelty, as the algebra of the frozen bands says (2.45). (6) The highest M was 0.779: still only the `support` band, still nothing at 0.80.

**A defect found and fixed: the runner called a correct outcome invalid.** `campaign_problem` required every campaign to add memory, so R03 (D0, A2 off, nothing written BY DESIGN, 2.38) ended "INVALID RUN" with exit 3 and no report, although every field above is the registered behaviour. Fixed: a campaign the ENGINE ran at D0 is not required to write; a failed agent or a non-completed run is still invalid, and under the requested policy every campaign must write as before (`writes_expected`, tested). The evidence is also now printed BEFORE any verdict on the campaign, so a verdict cannot hide it.

### 2.47 The freeze tag (6 November 2026)
Tagged `phase7-freeze-20261106` at commit `8961598` and pushed, as the owner ordered ("the COUNT live run, the `Architecture.md` and `Memory.md` injection descriptions, then the freeze tag"). Verified at that commit: ruff, format and mypy clean; backend 1747 non-DB and 723 DB tests; frontend lint, typecheck and 75 tests. The engine is COUNT (Registration 2 Amendment 1), D0 is one fresh query, A2 runs iff the depth is not D0, injection reaches A6 only (`a6_copy@v3`, cap 12), the evaluation has its own budget (660 + 300), the caps are audited, the sequence rule and the registered memory-versus-novelty analysis are in place. **CORRECTION (7 November 2026, entry 2.51): "mypy clean" covered `app` only.** CI's mypy scope was `files = ["app"]`, so `scripts/` was never type-checked; two errors in `scripts/phase7_stage2.py` were already present at the tag. Ruff and format did cover `scripts`. **A change to code under this tag needs a decision-log entry** (this entry and later documentation commits are not code). **Open, deliberately not blocking the freeze (owner):** the A3 invalid-output re-read against the 22 percent baseline after the next real runs, R26 to R30's last attempt, the removal of the python.org install. **Not yet done and not part of the tag:** the end-to-end Playwright job was not run locally; CI runs it on the pushed branch.

### 2.48 The evaluation harness: Baseline A defined, the arm plumbing (a freeze amendment), a resumable generation run, the dry run (6 November 2026, owner)
**Found first (6 November, evening): the harness did not exist.** The arms were defined in `evaluation-plan.md` section 1 and `runs.arm` existed, but nothing ran them: no arm field in the request, no Baseline A (a "single LLM call" with no implementation or prompt), no way to stop the baselines from recalling and writing memory (the worker's pipeline always did both), nothing connecting the re-run policy to a driver. So the dry run first required building it. **The harness is NEW CODE WRITTEN AFTER THE FREEZE and is the code that runs the experiment; it carries the same risk as anything frozen, and the dry run is its only check before about 660 calls depend on it.**

**1. Baseline A, as ruled (amended from my proposal).** It receives the brief INCLUDING the audience, the verified (and deduced) claims, and the brand's voice attributes. It does NOT receive research findings, memory, personas, positioning or market analysis: those are products of the agents, and are what the comparison is about. One Standard call (`baseline_a_copy@v1`, `app/agents/baseline.py`), through the same sector gates, validator and approval; no A7, so no advisory (as the 2026-10-29 ruling already said of a single call). **THE PROMPT IS WRITTEN TO THE SAME STANDARD OF EFFORT AS EVERY OTHER PROMPT: no deliberate under-specification, no shorter instructions than A6 gets for the equivalent job. Recorded as a constraint because a baseline the author also designed invites suspicion.** It carries `a6_copy@v3`'s rules WORD FOR WORD: of A6's eight rules the baseline has six verbatim plus one reworded (the voice rule, whose referent, the positioning, is absent) and omits only the two that describe inputs it does not receive (the category notes and the remembered notes). A test (`test_arms.py`) asserts exactly that: three A6 rules absent, one added, the opening, the format line and the revision rule identical; and that the prompt contains none of the agents' products. The draft becomes copy through the same function A6 uses (`creative_from_draft`), so the arms' copy is assembled identically. *Voice, stated so it is not discovered later:* the test set's brands have no voice attributes (`brands.voice_guidelines` is empty for all), so Baseline A reads "(none given)", as does every arm effectively; **no other agent receives `voice_guidelines` today**, so if voice attributes are ever populated Baseline A would hold an input A6 does not. I argue for no change now (the field is empty), and flag the asymmetry for the day it is not.

**2. THE FREEZE AMENDMENT (owner-approved): the arm field and the per-arm memory gate.** Plumbing the registered design requires, not scope (A1 injection, component 5 and the reuse bands stay out). Built: `RunRequest.arm` (default `proposed`) written to `runs.arm`; the arm travels in the run state; **enforced in the graph, not left to how the harness calls it**: Baseline B is D2 with all agents whatever depth was asked (even `necessity`); both baselines neither recall (step 4 or step 8), inject, nor write memory (step 15 is skipped); Baseline A skips research, has no specialist agents and no A7. Conditions met: this entry; **a regression proof, not an assertion**: `tests/test_arms.py` replays the COUNT live run's recorded inputs (`evaluation/regression/count_live_run_20261106.json`, read from the run checkpoints) through the pipeline as it now is and asserts the same depth decision, the same R and U, and the same injected items (id, band, M, over cap, below floor) for all three campaigns: it passed. **A NEW freeze tag follows the harness, so the tag names the code that actually ran the evaluation**, not code that never did (the tag `phase7-freeze-20261106` marks the Phase 7 code before the harness).

**3. THE GENERATION RUN MUST BE RESUMABLE (owner).** About 350 calls over four hours unattended, on a machine that lost six days to power, DNS and the provider, is the most fragile event left. If it dies at campaign 40 of 60 it must continue from 41, not restart: a checkpoint per campaign, an explicit resume path, tested by killing the process mid-batch (as the worker was). Built before the dry run and exercised in it.

**4. The dry run.** Two requests across all three arms, about 40 to 50 calls, on a THROWAWAY database (`brandverse_dryrun_eval`, created from the real migrations, registered as an evaluation database through a temporary budget file so `config/evaluation_budget.yaml` and `brandverse_evaluation` are untouched); the target is printed and the run REFUSES unless it is the throwaway, before the first call. **A dry run against the evaluation database would create evaluation data and break every "no evaluation data existed" statement recorded against each amendment.** Its calls fall under the evaluation scope of a throwaway database, outside the development ceiling (497 of 700), and are LOGGED here so they are visible: see the dry-run entry when it is made.

**5. Schedule accepted** (the harness build 6 to 8 November; dry run 9 November; generation 10 November; sittings 11 to 14 November; buffer 15 November; rating 16 November, fixed), with the note above that the harness is new post-freeze code.

**6. The resumable generation run, as built (6 November, evening).** `app/evaluation/generation.py`; tests `tests/test_generation.py` (12), including a REAL subprocess killed while campaign 5 of 60 is in flight and then resumed. **What exists:** the registered order filtered into four waves, never reordered (`baselines` = 60; `proposed:1`, `:2`, `:3` = 10 each); an append-only fsynced checkpoint (an `intent` line before a run starts, `start` with its id, `end` with its outcome; a line cut off by the kill is ignored, a corrupt line in the middle is an error); resume skips settled items and RECONCILES an in-flight one against the run's real status (finished while the harness was dead: recorded, not repeated; still running: waited for, so no second run beside it; worker gone: recorded `interrupted`, started again; killed between "run started" and "id recorded": the orphan is found and adopted, so a kill cannot create a second run). **Rulings I made that you may reverse:** (a) an `interrupted` attempt does NOT count against the 2-re-run cap, because the cap is for provider outages (`reruns.py`) and a killed harness is not one; interrupted attempts are listed and their calls stay in the ledger. (b) The Proposed waves 2 and 3 are REFUSED in code until `Runner.decided("proposed", N-1)` is true (memory is written on approval, T6), so the order cannot be broken by the person typing the command. (c) A third infrastructure failure of one campaign stops the whole batch and comes to you, as the re-run ruling says. **NOT built yet, and said plainly:** the real `Runner` (API and database: seeding the ten brands' organisations and products, starting runs, waiting, reading `runs.status`/`failure.invalidated`, and `decided` from `approvals`) and the command that wires it. It is the I/O half and cannot be checked without the throwaway database, so it is the first thing the dry run builds on; until then the driver is proved against a file-backed fake only. **Also open:** the generation run reads no approver decision, so who decides campaign N of the Proposed arm before round N+1 (a human, T6) is the schedule's constraint (evaluation-plan "Presentation order"), not something this module can shorten.

### 2.49 Rubric amendment A2: the human instrument rebuilt, brand consistency dropped (6 November 2026, owner)
`docs/rating/rubric-amendment-2.md`, written, **not frozen** (A1 never was; no `.lock` exists). Provenance recorded first: the 6 November round was rated verbally by two humans and processed by an AI tool, so there is NO primary record, the session is formative only, and no score, agreement or kappa from it is reported anywhere. Changes derive from A1's anchor wording alone: feasibility ("would a marketing team publish it", facts excluded), product relevance (specificity, not truth), customer relevance (0/1 merged), trustworthiness removed (truth comes from the gates), raters no longer tick gates (they cannot see what the gates need), outcome kept. **Brand consistency is DROPPED under the owner's own condition:** no evaluation brand has a voice attribute (`test_set.yaml`, `tuning_brands.yaml` and `brands.voice_guidelines` are all empty; the only stated voices are the three fictional memory-relevance brands, not in the evaluation). Result: four dimensions (product relevance, customer relevance, originality, feasibility) plus the outcome; both sheets regenerated. **Originality is carried over unchanged** (not in the owner's list) with A1's known defects (section 4, item 4) stated, not fixed. The outcome wording ("Would it be defensible to publish this?": approve yes as it stands, revise yes after changes, reject no) is mine, taken from `approval-rubric.md` section 1, because the gate-based definition is no longer visible to raters; **please confirm or replace it.** Protocol for 16 November (own hand, photographed, literal transcription, no AI tool touches rater output) is in the amendment and must go into the runbook. `scripts/freeze_rating_amendment.py` still freezes A1's path only.

### 2.50 Owner's answers to 2.49; the real Runner; two post-freeze defects found by reading it (7 November 2026)
**A. Owner's answers to 2.49, recorded.** (1) **Brand consistency dropped, confirmed, recorded as a SYSTEM limitation, not a rubric one:** no evaluation brand carries voice attributes and the brand-voice similarity gate is unimplemented (section 3.5; it carries no V number, and none is invented here), so there is nothing for the dimension to measure. Four dimensions plus the outcome. (2) **Outcome wording accepted, with one addition**, now on both sheets and in A2: "Feasibility rates the WRITING. The outcome is your decision as the brand's approver, all things considered. They can differ, and often will." (Otherwise the outcome duplicates feasibility's "a marketing team would publish it", the duplication that removed trustworthiness.) (3) **Originality carried over, defects stated, plus a risk note:** it scored at or near zero almost everywhere on 6 November; a floored dimension gives no discrimination and an unstable kappa; **if it floors across all three arms on the 16th, that is reported as a finding about the system's output, not as a rating result.** (4) **A harness kill does not count against the re-run cap** (the cap is for provider outages, which are external to the system under test), kept as built, and written into the runbook. (5) **Proposed waves are refused until position N-1 has a human decision**, enforced as built; the runbook now says in plain words that generation of Proposed round 2 cannot begin until every round 1 campaign is approved or rejected, round 3 likewise, **so the approval sittings are the serialising step of the whole evaluation.** (6) **Joint calibration added to 16 November:** both raters score three campaigns TOGETHER, discuss, then score the 45 independently in silence; the three are excluded from the kappa (A2 section 2, runbook). (7) The own-hand, photographed, literal-transcription, no-AI protocol is in the runbook as well as A2. A2 remains **not frozen**.

**B. The real Runner (`app/evaluation/generation_runner.py`).** Rulings I made, reversible: **one organisation per (brand, arm)** (a brand's three Proposed campaigns share one, which is what lets campaign 2 read what campaign 1's approval wrote; the Baselines' organisations cannot reach it by construction, on top of the graph's refusal); **the harness submits as owner and editor and cannot approve** (a different user holds the approver role; the class has no approve method); `decided(arm, N)` is true for a request when its latest approval is `approve` or `reject` (`revise` is not final), or when its run ended without parking (blocked, partial, a failed run with no infrastructure kind: nothing is owed, so the next round is not held forever), and false when a request has no run yet or only invalidated ones; a run is found by what it is (organisation, arm, product, brief), never by a remembered id; `failed` with an infrastructure kind is `invalidated`, any other ending is a result. The target is printed and refused unless it is a loopback host and the throwaway database, and the server is then asked which database it reached.

**C. TWO POST-FREEZE DEFECTS FOUND BY READING THE CODE THE RUNNER DRIVES, before any call (both are code under the tag `phase7-freeze-20261106`, changed here with this entry):** (1) **The worker's `CallLedger` was built with the development budget only.** The evaluation scope (2.36) existed in the ledger but `open_pipeline` never passed it, so every call from an evaluation database would have been refused as `database_unregistered` on the first day of the evaluation. Fixed: `evaluation_budget=load_evaluation_budget(settings.evaluation_budget_path)`; the path is a setting only so the dry run can register its database through a temporary file. (2) **The frozen per-request brief check never reached a run.** `Pipeline.start(brief_check=...)` existed and `evaluation/brief_checks.json` is frozen, but `run_pipeline` never passed it, so each of the 90 runs would have computed its own brief check (90 calls, not 30, and not identical across arms: the confound the freeze exists to prevent). Fixed server-side, never from the caller: an evaluation worker (`EVALUATION_RUN=true`) looks the record up by the brief's hash and refuses a brief that has none. Tests: `tests/test_generation_runner.py`.

**Headline (owner, 7 November): both defects in C would have stopped the evaluation on its first day, and both were found by reading, not by testing.** Neither the unit tests nor the green CI at `8961598` could have found them (CI holds no evaluation database, and no test ran a whole run through the worker with the evaluation's configuration). That is the justification for the separate reading pass of the rest of the evaluation path, entry 2.51.

**D. Dry run (7 November 2026, 08:06 UTC; record `evaluation/dry_run/dryrun_20261107T080628Z.json`).** Target printed and verified before the first call: `127.0.0.1:55432`, `brandverse_dryrun_eval` (the server answered as that database), created from the real migrations; ledger scope registered through a temporary budget file with a ceiling of 80; `config/evaluation_budget.yaml` unchanged (checked). **35 Standard calls** (30 ok, 3 error, 1 invalid_output, 1 provider_unavailable; 0 refused), 6 runs: B01 R01 and R02 across the three arms. On battery, no worker crash.
*Exercised and PASSED:* (1) the harness was **killed while R01 Baseline B was in flight**; the resume reconciled that run against the database (`reconciled: [R01:baseline_b]`), did not repeat it, skipped the finished Baseline A, and started the remaining two (`started: [R02 baseline_b, R02 baseline_a]`, no interrupt, no re-run). (2) `proposed:2` was **refused twice**, before round 1 existed and again while round 1 was generated but undecided, with the T6 message. (3) The **submitter was refused 403** when it tried to approve; the stand-in approver (throwaway only) got 202 and the decision was recorded. (4) A call from the evaluation database was **admitted by the ledger** under the temporary evaluation budget (the wiring fix of C): 0 refused. (5) **No model call was made at or before step 4 in any of the six runs**, so every arm read the frozen brief check (C, second defect) and none computed its own. (6) The orgs, brand, products and claims seed worked, and a harness restart found them by name.
*Caught by the dry run (not by reading):* (a) **Round 1's Proposed campaign completed with declared gaps: A2 and A3 failed with `LLMCallFailed`, infrastructure kind `network_error`** (the same step in Baseline B's R01 run also failed on A2, and a provider 5xx hit its A6). The registered invalidation fires on `provider_unavailable` only, so this run was NOT invalidated, the driver parked it as a normal result, and a campaign with gaps **writes no memory**: 0 memory items exist, so **round 2 read nothing (`considered: 0`) and the memory leg of the design was NOT demonstrated by this run.** This is exactly what stage 2 found on 5 November, and the generation driver has no equivalent of stage 2's guard (a Proposed campaign must end whole, with no failed agent, having added memory). It is finding **R10** below. (b) **My own check was wrong:** it compared `runs.brief_check`, written only when a run ends, so it read null for every parked run and reported "the arms do NOT carry one frozen brief check". That was a false alarm from the instrument; the step-4 evidence in (5) is the correct test and the script now uses it. The JSON record keeps the original false finding, uncorrected, because it is the record of what the run printed.
*NOT exercised:* the memory read (see a); a Baseline or Proposed run INVALIDATED by `provider_unavailable` and re-run (none occurred); `find_orphan` on a real kill between "decide to start" and "id back" (the kill landed after the id; that path is proved only against the fake); the 90-run scale, B07 (regulated, strong tier) and the Baselines' approval (nobody approved the four Baseline runs, which stay `awaiting_approval`, so their `runs` columns for depth, calls, brief check and latency are empty until they are decided: any reader of those columns must wait for the decisions).
**R10 (found by the dry run).** `network_error` gaps (and `rate_limited`, `quota_exhausted`) do not invalidate a run, by the ruling of 5 November that only `provider_unavailable` is ruled; in the evaluation such a gap produces a Proposed campaign with no memory write, which silently changes what rounds 2 and 3 read, and costs the arm quality it would not have had. Options for the owner: extend invalidation to `network_error` (it is already one of the four infrastructure kinds fixed on 17 October, which says its runs are re-run); or have the driver refuse to treat a Proposed campaign with gaps as settled. Not changed.

### 2.51 The mypy record, the A2 freeze, and the reading pass over the rest of the evaluation path (7 November 2026, owner)
**1. The mypy errors in `scripts/phase7_stage2.py`: they PREDATE the tag, and the tag's record was true for what CI checks.** The file is byte-identical at `8961598` and now (`git diff` empty). `backend/pyproject.toml` sets `[tool.mypy] files = ["app"]`, and CI runs bare `uv run mypy`, so `scripts/` and `tests/` are never type-checked. The two errors (line 755, `dict[str, list[CampaignScore]]` passed where `dict[str, Sequence[CampaignScore]]` is expected; line 980, an optional row indexed) were there when the tag was cut and were not seen because nobody ran mypy on that path. So: nothing changed under the tag, and the entry 2.47 sentence "ruff, format and mypy clean" is TRUE ONLY OF CI'S SCOPE (`app`; ruff does cover `scripts`). **The freeze is not less verified than recorded for the code that runs the evaluation (`app`), but the scripts under the tag were never type-checked, and 2.47 should have said so.** Not fixed in this entry: they are in a registered file (`phase7_stage2.py` is in its own `REGISTERED_FILES`), so a change is the owner's call; the options are widening mypy to `scripts` (about two errors to fix first, in a file whose output is a recorded result) or leaving the scope and recording it. **Owner's ruling: widen the scope and fix them in a behaviour-free commit. DONE, with one finding about the scope.** Widening to ALL of `scripts` reports **37 errors in 17 files, not two** (probes, the recorder, posture and the search-rate tools, plus tests they import), including one that is a real bug and not an annotation: `scripts/phase7_r_check.py:115` reads `RequestInputs.regulated`, an attribute that does not exist, so that stage-1 tool would raise `AttributeError` when it reaches it. The scope set is therefore `app` plus the scripts that RUN or REPORT the evaluation (`phase7_stage2`, `phase7_decomposition`, `phase7_count_predictions`, `evaluation_dry_run`, `freeze_rating_amendment`, `chunk_f_first_campaign`, `chunk_f_agreement_round`), clean at 130 source files; the other 15 files' errors are LISTED here and not fixed, which is the owner's call (they are not on the evaluation path; `phase7_r_check` needs a decision because fixing it changes behaviour). The fixes changed no behaviour: `interim_verdict` takes a `Mapping` (it only reads), the `ended` row is narrowed with `is not None` instead of `bool(...)`, `decompose`/`from_record` accept `Mapping[Any, float]` weights (the config's key type is a Literal). `mypy` also needed `explicit_package_bases` and `mypy_path` for `scripts` to be checkable at all.

**2. Rubric amendment A2 is FROZEN** (7 November 2026, 08:09 UTC, `docs/rating/rubric-amendment-2.lock`), before the evaluation generated any campaign (the dry run's output is in a throwaway database and is not evaluation data). `scripts/freeze_rating_amendment.py --amendment 2` extends the freeze to A2 and, because the instrument the raters hold is the sheets as much as the text, the lock also carries the hashes of `rubric-sheet.html` and `scoring-sheet.html`. `tests/test_rating_amendment_lock.py` fails if any of the three differs. A change is A3, never an edit.

**3. The reading pass: findings, listed first and NOT fixed** (see the report of this date for each with its evidence; the list is repeated in the owner's reply and each is marked with what it breaks).

*Where code and registered design DISAGREE (each found by reading; none touched):*
* **R1. The run order is registered as interleaved arms and EXECUTED as arm blocks.** The ruling of 5 November ("for each request the three arms run adjacently, so a provider outage lands across arms") is what `run_order.yaml` encodes, but `generation.py` filters it into four waves: all 60 Baseline runs, then Proposed round 1, 2, 3. A provider outage during the Baseline wave now lands inside the Baselines and in no Proposed run, which is the confound the ruling exists to remove. The cause is real (round N+1 of Proposed needs round N approved by a human, T6) but it applies to the Proposed arm only: the Baselines of position N could be generated in the same wave as Proposed round N, in the registered order, which restores adjacency within each round. Not done.
* **R2. No read selects the PRIMARY attempt.** `reruns.primary` and `reruns.invalidations` are called by nothing, and nothing records which run replaced which (there is no `rerun_run_id` column; the Runner finds a request's runs by organisation, arm, product and brief). Every evaluation read (`run_outcomes`, `latency`, `llm_attempts`, `provider_retries`, `outages`, `necessity_records`) is over every run of the organisation, so a run invalidated by an outage is counted in ARR, CR and LR, and `run_outcomes` reports it as a `failed` run of its arm, which is what Amendment 1 says an invalidated run is NOT (it is not a result). With any invalidation the reported figures would be wrong without an error.
* **R3. `outages.RUN_SPANS_SQL` measures a run from `created_at` to `completed_at` (or now()), which includes the hours a parked run waits for its approver.** Every run parked across an outage window is reported as having touched it. The wait is recorded (`run_waits`) and the span should exclude it or use the run's call times.
* **R4. `necessity_decomposition.necessity_records` is unfiltered by arm and by attempt.** Baseline campaigns carry `strategy.necessity = {policy, depth, requested}` (nodes.assess_research_necessity), so they are returned, and `from_record` raises `KeyError: 'novelty'` on the first one. The docstring says "every evaluation campaign that carries one"; the analysis needs the Proposed arm's primary attempts only.
* **R5. There is no ARR / SRR / CR / LR calculator.** The inputs are read piecemeal (call totals, searches in `runs.search_count`, latency components) but nothing pairs the arms per request, picks the primary attempt, computes the four formulas, breaks them down by campaign position, or prints the two-way sensitivity (with and without provider-retry campaigns). The reported numbers of the thesis have no code yet. This is a build item for after generation, and it is the code that R2 makes dangerous.
* **R6. `pages_fetched` is not persisted per run.** The research report holds it (`adapters.py`), but `RunFacts` carries only `search_count`, so the LR fetch component (count difference x fetch wall time per page) cannot be read from `runs`; the only copy I found is in the LangGraph checkpoint state.
* **R7. A runbook step where a refusal belongs.** The "Starting the evaluation worker" list omits `RESEARCH_SNAPSHOT_DIR`, and `EVALUATION_RUN=true` does not require it: without it `open_pipeline` installs `_refuse_live_research`, so every D1 and D2 run (Baseline B and Proposed) fails. The dry run sets it; the runbook does not say to. The runbook's own principle is that such a step becomes a start-up refusal.
* **R8. The snapshot does not cover R26 to R30.** Known and open (R26 to R30's last attempt). The consequence is now concrete: replay is keyed by query, a miss raises `SnapshotMiss`, which is not an infrastructure failure, so those runs of Baseline B and Proposed would END as the arm's own failures and not be re-run. Either the recording completes first or those requests are declared out of the experiment before generation; they must not run and be counted.
* **R9. A run that does not settle inside `wait_timeout_s` makes the driver start a SECOND run.** `generate` writes whatever `wait` returns as the attempt's `end`; a timed-out wait returns `running`, which is not `done` and not counted, so the loop sees no counted attempt and starts the item again beside the first (which may still be queued, and which the worker's recovery sweep may later resume). The fake runner's `stuck` case is tested only through reconciliation, never through this path. This is in the Runner I wrote today and in the driver; the right behaviour is to record `interrupted` and stop the batch for the owner.
* *Checked and found to AGREE:* the order file against the code (`test_run_order.py`); that the necessity decomposition's algebra, bands and weights are pinned by a test against the live configuration; the claim that spend, work and re-executions reconcile exactly; approvals reads exclude calibration, tuning, practice and test rows in code; the re-run cap counts only infrastructure outcomes; routing and agent-selection absence are recorded as limitations (line 612), not hidden.

### 2.52 Fixes ordered by DATA LOSS, not severity (owner, 7 November 2026): what changed and what each change touched
**Rule (owner): priority is set by data loss.** Before generation: what cannot be recovered afterwards. After generation is safe: what reads stored data.

**A. THE AUDIT: what the analysis needs that lived only in the LangGraph checkpoint (the question the owner asked of R6).** Comparing `RunState` (`app/orchestrator/state.py`) with what any row holds:
* *Already on a row:* depth, R (`research_score`), selected agents, `memory_hit_count`, `search_count`, declared gaps, the brief check, timings, the failure (all `runs`); the strategy with the necessity record, the injection, the market output and the claim inventory, the creative and the validation (`campaigns`); memory written (`memory_items`); every call (`llm_calls`, with step, revision round, tokens, latency); the waits for a person (`run_waits`); decisions (`approvals`).
* *In the checkpoint ONLY, and now persisted (migration 0048):* **`pages_fetched`** and `pages_served` (the LR fetch component); the rest of the research report (`partial`, `uncovered_facets`, pages discarded for score); **the memory read** (`memory_coverage`, `memory_slots`, `memory_coverage_final`, `memory_persona_scored`, `memory_similarities`: the plan reports coverage and "k of 2 slots" by position and none of it was on any row); `agent_rationale` (why each agent ran); the final `revision_round`; and **`findings`** (url, score, components, first-party flag; for Baseline B and Baseline A, which write no memory, they existed nowhere else).
* *Still in the checkpoint only, NOT persisted, by judgement:* the persona CONTENT of the arms that write no memory (Baseline B; the raters see copy only and persona ids are in `strategy`), and the full text of agent outputs other than A1's positioning and A2's market (in `strategy`). If the analysis later needs either, it is the owner's call to add them before generation.
* *Also found:* `runs` columns such as `depth`, `llm_call_count` and `latency_ms` were written only when a run ENDED, so a run parked for days showed them empty. They are now written at park too.

**B. What was changed, and what each change touched** (all tested; ruff, mypy 130 files, and the full suite run before the commit):
1. **Migration 0048** (`supabase/migrations/0048_runs_record_and_supersession.sql`): `runs.pages_fetched`, `runs.pages_served`, `runs.facts` (jsonb), `runs.supersedes_run_id` (same-organisation foreign key, unique, not self); `app.runs_initial_state()` replaced to force all four null for an API role. Touches the live schema: **NOT applied.** Architecture §9 row added, `supabase/dry-runs/exercise_0048.py` written, local DB tests pass (`tests/db/test_run_record_and_supersession.py`). The live dry run, the CI-green push and the apply are the remaining steps of the migration procedure and are NOT done.
2. **Persistence code** (`budgeted_run.py`, `orchestrator/runner.py`): `RunFacts` gains `pages_fetched`, `pages_served`, `facts`; `suspend_run` writes the run's facts when it parks (`_PARK_FACTS_SQL`, coalescing so a second park never blanks); `end_run` writes them again; `facts_from_state` builds them. `RunLedger.suspend` takes an optional `facts`; the test fake was updated.
3. **Supersession at run time** (`app/api/runs.py`, `link_supersession`): after the run row is inserted, the BACKEND links it to the infrastructure-failed run it re-runs (same organisation, product, arm and brief; the earlier `failed` with an infrastructure kind; not already replaced). Derived, never supplied by a caller. The selection logic that reads it is NOT built (after generation).
4. **R4** (`necessity_decomposition.necessity_records`): arm = `proposed`, the record must carry `novelty`, superseded attempts excluded. The old test gave the baseline NO record; the new one gives it the real shape.
5. **R9** (`generation.py`): a run that does not settle stops the batch, recorded `interrupted`, nothing started beside it; a resume checks that run first. Touches the driver's `_reconcile` (a still-running run used to be recorded and then RESTARTED).
6. **R7** (`Settings`): an evaluation worker with real agents refuses to start without `RESEARCH_SNAPSHOT_DIR` naming a directory with `committed.jsonl`. Touched `tests/test_cap_audit.py` (which built evaluation settings without one).
7. **R1** (`generation.py`, tests, dry-run script, runbook): the waves are now THREE ROUNDS (`round:1`, `round:2`, `round:3`), each with all three arms of every request adjacent in the registered order; the earlier "baselines, then proposed:1..3" waves are gone. The refusal now applies to the whole of round N+1 until round N's Proposed campaigns are decided. This **supersedes 2.48 item 6's waves**. The schedule is slower and accepted.
8. **R3** (`outages.py`): a run's span for outage overlap is its active time (its waits, from `run_waits`, removed); `runs_touching` now collects windows across a run's several spans.
9. **mypy** widened to the evaluation-path scripts (commit 0fc349a, above in 2.51).

**C. Not changed, awaiting the owner:** **R10** (a Proposed campaign that completes with `network_error` gaps writes no memory and is not invalidated: found by the dry run, not in the owner's list). R2 selection logic and R5 (the calculator) are scheduled after generation is safe.

**D. R8 (owner's ruling): attempt the R26 to R30 recording tonight if the network is healthy; HARD DEADLINE the end of 9 November.** If not committed by then: exclude B09 and B10, register the design as 8 brands x 3 campaigns BEFORE generation starts, record the reason (the standing rule: cut brands, never campaigns per brand). In the runbook. Generation does not start while this is open.

### 2.53 Amendment 3, sequence integrity, the metrics calculator, and what the `phase7_r_check` bug could and could not have changed (7 November 2026, owner)

**1. AMENDMENT 3: every registered infrastructure kind invalidates** (`docs/evaluation-plan.md`, Amendment 3, dated and with its no-data check). `INVALIDATING_GAP_KINDS` is now the kind list itself (`rate_limited`, `quota_exhausted`, `network_error`, `process_crash`, `provider_unavailable`), so the set cannot drift from the registered list. The owner's reasoning, recorded: the infrastructure list exists to separate what is not the arm's behaviour from what is, and a local DNS fault is no more a property of an arm than a provider outage. The re-run cap is unchanged. **This also settles R10** (the dry run's finding: a Proposed campaign completed with `network_error` gaps and wrote no memory): such a run is now invalidated and re-run, and would not have been parked as a result. **`rate_limited` is additionally reported as a SCHEDULING DEFECT every time it binds** (`metrics.scheduling_defects`, printed even when zero): Baseline B makes more calls than the Proposed arm, so a binding rate limit binds unevenly across arms and would look like an efficiency result. A free-tier WAIT is not a defect. **No-data check, run today, not recalled:** read-only against the live project, 0 runs, 0 `llm_calls`, 0 campaigns, 0 approvals, 0 memory items, 3 organisations all `is_test`; and `brandverse_evaluation` does not exist, the only EVAL organisations anywhere being the dry run's 6 runs in its throwaway.

**2. SEQUENCE INTEGRITY, which the owner rates above the classification.** A Proposed campaign at position N that wrote no memory (other than engine-chosen D0) now BLOCKS position N+1 for that brand: an approval is not enough to release the next round. Without it a brand quietly becomes a no-memory arm inside the Proposed arm and nothing downstream reveals it — the depth is high for the honest reason that no memory exists, and the write-up would report that as the mechanism. `app/evaluation/sequence.py` holds the rule (the stage-2 runner's rule), the driver refuses on it (`Runner.sequence_problems` beside `Runner.decided`), and `tests/test_sequence_integrity.py` drives BOTH the shared module and the frozen `phase7_stage2` copy over one table of cases so reuse is a test, not a claim. The registered file was not edited.

**3. R5 AND R2's SELECTION LOGIC BUILT** (`app/evaluation/metrics.py`, `scripts/evaluation_metrics.py`, 23 + 4 tests). Reads stored rows only, zero model calls. **ARR, SRR, CR and LR**, arms **paired per request** (a request missing any arm's primary attempt is listed as incomplete and excluded, never silently dropped), the **primary attempt selected** by the registered rule (the earliest attempt that ended without infrastructure failure) with `runs.supersedes_run_id` **cross-checked and disagreements reported rather than resolved silently**, a **breakdown by campaign position**, and the **with/without-provider-retry sensitivity**. Calls are WORK not spend; cost is labelled list-price-equivalent; LR keeps its four parts apart (search and fetch DERIVED from unit costs read from the recording's committed lines, model and local research OBSERVED) and labels the total partly derived. The depth REDUCTION, three-band SEPARATION and memory-versus-novelty attribution are emitted in one block with no way to print one alone, and the quality sample (15 requests, 45 campaigns) is declared in the report. **The test the owner asked for by name:** a request whose first attempt an infrastructure failure ended and whose re-run succeeded contributes the re-run's numbers and only those, asserted on every figure including LR's derived parts.
*Three findings from building it, stated rather than papered over:* (a) **the run row carries no evaluation request id** — the brief text is the only link between a run and the design, so the reader resolves it through the test set and REFUSES a brief no request registers (a wrong scope must not read as an empty one); (b) **extraction is not timed separately** — `timings.research_other_ms` is replayed fetch, extraction and scoring together, so the plan's "extraction, observed" is reported under that honest name and not as extraction alone; (c) **R01 to R12 of the recording predate the fetch instrumentation**, so the LR fetch unit cost is measured from the later lines only, and a snapshot with no instrumented line REFUSES to give a unit cost rather than returning a quiet zero.

**4. `scripts/phase7_r_check.py`: the bug could NOT have changed any recorded result. Stated explicitly, with the evidence, and the file is left alone.** Two independent reasons, each sufficient:
* **The bug is FATAL, not silent.** `r_in.regulated` raises `AttributeError` on the FIRST loop iteration (R10), before a single table row is computed or printed; the only output would be the memory-count line and a traceback. Verified by running the attribute access (`RequestInputs` has ten fields and no `regulated`). **A crash cannot produce a wrong number; it produces no number.** So no recorded figure can have come from the buggy code path.
* **Even fixed, the value is identical for the only brand this script runs on.** The script hardcodes R10/R11/R12 and the `brandverse_chunk_f` database, i.e. brand B04 (footwear, `regulated: false`); **B07 is the only regulated brand in the test set.** So `1.0 if r_in.regulated else 0.0` evaluates to 0.0 — exactly the constant `s = 0.0  # B04 footwear is not a regulated sector` that the WORKING version carried.
* **Provenance of the recorded numbers, checked in git:** entry 2.20's stage-1 figures came from commit `8d7b4f8`, whose line is that hardcoded `s = 0.0`; the attribute access was introduced later, in `44e6977` (the registration-2 edit). The recorded result therefore came from code with no attribute access at all.
* **And the recorded numbers reproduce exactly from the frozen configuration**, independently of which tool emitted them: with N 0.214 and 0.223 I recomputed R = 0.4489 and 0.4527 (2.20 records "0.449 and 0.453", D1/D1 under the bands in force then, D0 below 0.35 and D2 from 0.70), and the re-score in `phase7-first-decision.md` (n = 1 of 5, then 0 of 5) gives R 0.4191 and 0.4527, D2/D2 — matching the recorded "R 0.419, 0.453: D2, D2" exactly. **No registered prediction, interim verdict or decision-log figure is affected.** The file stays as it is, per the owner's instruction, and the 15 unchecked script files stay listed and untouched (owner's ruling 5).

**5. Still open:** R2's *reporting* of invalidations (the selection is built; `reruns.invalidations` is still called by nothing), and migration 0048's live apply.

### 2.19 Who approves the ten campaigns for 8 November
The owner's instruction was to "approve them yourself first and record how many you revised". **The approver is a human (`Rules.md` T6;
`approvals.approver_id` is a member holding the approver role): an AI cannot approve a campaign**, and a revise count produced by an AI reading would not be the human approver's
revision rate, which is the quantity the sitting estimate needs. The assistant generates the ten campaigns and prepares them in readable form; the revision rate is
Kundan's own decisions on them, recorded in the tuning organisation's `approvals`. An assistant pre-read against the rubric may be offered, labelled as that, and is not a count.

### 2.14 The 2026-11-01 sitting with the two raters is a FAMILIARISATION session, NOT the D3 calibration record
**Recorded 2026-11-01 (owner).** The two human raters met for a familiarisation sitting. **It is not the calibration round of
`docs/approval-rubric.md` section 6, and no score produced in it is recorded as data.** Reasons, both factual: (1) **only one
real campaign exists** (chunk F's first, 2026-11-01), against the ten practice campaigns calibration needs, on brands in
neither the evaluation nor the tuning set; (2) **blind presentation is not built** (arm labels stripped, constrained
randomisation, `docs/approval-rubric.md` section 2), so a sitting today cannot be blind to arm. The material used is in
`docs/rating/`: a rubric sheet, a blank scoring sheet, and six hand-written practice pieces about fictional products that are
not in `evaluation/test_set.yaml` and are not the output of any arm, so the raters meet no evaluation copy before rating it
blind. The scoring sheets from the sitting are not entered into `approvals` (`kind = 'calibration'` rows are the calibration
record, and none is written); the calibration round remains booked for 8 November and the main rating round for 16 November.

### 2.15 What the rubric sheet could not fill in (found 2026-11-01, building it)
The sheet was to give, for each scoring dimension, a definition copied from the rubric and a documented scale. **Two
corrections and one finding.** *Correction 1:* S, P, C, A, F and E are the **memory score's** components (`Memory.md` section
3.1), computed by software for a memory item against a query; no person scores them, so they are not on the sheet. *Correction 2:*
the human instruments are (i) the approval rubric, fully documented as pass/fail checks and a three-way outcome, and (ii) the
quality rubric of `PRD.md` section 7.1. *Finding (V23):* for (ii), **product relevance, customer relevance, brand consistency,
originality and feasibility have no documented definition and no documented scale; trustworthiness has a method name ("hallucination
audit, manual") and nothing else**; evidence quality and cost efficiency are computed, not rated. Those cells are left blank on
the sheet, not filled in. Consequences: Cohen's kappa "per rubric dimension" (D2) cannot be computed for them as the project
stands, since kappa needs categories, and `campaigns.quality_scores` has no documented shape. The approval outcome and the
per-check pass/fail do have categories. Not fixed here: defining the dimensions and their scales is a methodology decision for the
owner and the raters, and must be made, and recorded under `docs/approval-rubric.md` section 7, before the calibration round.

---

## 3. Output gates and claim handling

### 3.1 Anti-patterns: specified, not implemented — **OPEN**
**Ruled 2026-10-18.** (Third instance of a gate claiming more than it performs: 3.5.)

Eight marketing anti-patterns were listed in the outcome table as blocking. No detector existed, so
they never blocked. They were **removed from the outcome table**, and rule T5 is recorded as unmet.

*Reason for removal rather than implementation.* Four of the eight cannot be detected from copy at
all — paid endorsement needs the commercial relationship, fictional price anchoring needs real price
history, vulnerability targeting depends on who is targeted, dark-pattern consent is about a UI flow.
Keyword lists for the remaining four would have been a second unbacked claim dressed as enforcement.

*Enforcement.* A test fails if any of the eight is added to the outcome table before its detector
exists. The documents that described them as enforced were corrected at the time; the sweep was
made by search and is not claimed to be exhaustive.

*Threat recorded (V3).* Only the human approver checks them; the miss rate is unmeasured.

### 3.2 Superlative detection has three outcomes — **BUILT**
**Ruled 2026-10-18; v1 list hash-locked the same day.**

- Unambiguous superlative, no cited source → **block** (30 phrases)
- Context-dependent superlative, no cited source → **flag for reviewer** (11 phrases)
- Any superlative citing a verified claim whose own text says the same thing → **pass**

*Reason for the third tier.* The original two-outcome design forced ambiguous words ("leading",
"the only", "ultimate") off the list entirely, because a match would block — excluding the most
common real violations. A gate that silently permits the commonest violation is weaker than one
that raises it for a human.

*Accepted false positives, named in the file header.* "no 1-year warranty" matches the "no 1"
pattern; "Best beforehand" matches "best" because the exclusion is only for "best before". These
were chosen, not missed. ("bestow" correctly does **not** match, and is recorded as a checked case
rather than an accepted cost.)

*Deliberate exclusions, named in the file header.* "clinically proven", "scientifically proven",
"doctor recommended", "zero side effects", "100% safe" are evidence and safety claims, not
superlatives, and belong to the claim gate. A phrase landing in two gates would be counted twice
and the findings table would stop adding up.

*Families are enumerated, never patterned.* "most X" and "highest X" are listed word by word. A
regex family would catch "most people" and block compliant copy, and a surprise cannot be versioned.

*Matching.* Multiword phrases match across space, hyphen, dash or nothing, so "market-leading" and
"bestselling" are caught. This was fixed after testing showed hyphens already matched but closed
compounds did not.

### 3.3 Disclaimer matching is verbatim with a declared normalisation — **BUILT**
**Declared 2026-10-17.**

Unicode NFC; runs of whitespace collapsed to one space; both ends stripped; case-sensitive;
punctuation significant. A trailing space or line wrap never fails; a punctuation change does,
because it changes prescribed wording.

A reworded disclaimer is reported as **altered**, separately from **missing**. The two are
different findings and a panel will ask which occurred.

*Reason for declaring it.* A gate that fails for an invisible reason is worse than no gate.

### 3.4 Sector rules are synthetic and labelled — **BUILT** (migration 0021)
**Ruled 2026-10-17.**

No sector carries rules cited from regulation. Every sector is flagged `illustrative` rather than
`authoritative`, and the flag is printed wherever a sector is named.

*Reason.* The contribution is the mechanism that enforces whatever rules it is given, not a claim
that the shipped rules are legally correct. Citing a real regulatory clause slightly wrong would put
a panel's attention on regulatory reading rather than on the architecture.

*Generalisation made during implementation.* The flag defaults to `illustrative` for **all** nine
sectors, not only the regulated one, because scoping it to one would have implied the other eight
were authoritative.

*Enforcement.* A database check requires every rule either to carry the exact synthetic-source
sentence, or a URL and a human author. Triggers prevent a sector being marked authoritative while
synthetic rules are active.

### 3.5 The brand-voice similarity gate: specified, not implemented — **OPEN**
**Ruled 2026-10-31 (owner), same treatment as 3.1 (T5).**

`Architecture.md` section 8 describes the brand-voice gate as the cosine distance between the generated copy and a brand voice
vector, plus a registry half. **Only the registry half runs:** required disclaimers must appear verbatim and a reworded one is a
block (`altered_disclaimer`, `voice_scoring_text` strips disclaimer text for a similarity that does not exist). The
similarity half does not: A1 builds no voice vector (`app/agents/product_brand.py`), no similarity is computed, and no
threshold exists. The design therefore described a gate that was half enforcement and half description.

*Third time a gate has claimed more than it performed:* the eight anti-patterns (3.1, no detector); the first-party
sole-evidence rule (V6, built and binding nothing on this evaluation); and this one.

*What was changed.* The gate is marked "specified, not implemented" in `Architecture.md` (layer map, section 7's A1 row, the
section 8 gate table), `Phases.md` (Phase 3 and Phase 6 notes) and `PRD.md` (the "Brand consistency" rubric row, which names an
automated check and is scored by the human raters until one exists). It is in no phase.

*Not shown.* Whether a similarity gate would catch off-voice copy; nothing measures voice, and the approver is the only check.

---

## 4. The relevance set

### 4.1 Protocol — **BUILT**
**Fixed 2026-10-18.**

Brief → corpus drafted from the brief → queries written from the brief → candidate pool → blind
labelling → freeze → only then any tuning.

- The pool is the union of two retrievers' top-k (BM25 and plain vector similarity), **neither being
  the hybrid ranker under test**.
- Candidate and query order are randomised; no scores, ranks or retriever identities are shown.
- Five duplicate queries are hidden in the sitting to measure the labeller's self-consistency.
- Labels freeze before tuning. If tuning later suggests a label was wrong, the label stands and the
  disagreement is reported.

### 4.2 [superseded] The corpus was drafted after the queries, and redrafted
**Reversed 2026-10-25.**

The protocol specified corpus before queries, so the corpus could not be shaped by them. A later
instruction reversed that order and the corpus was drafted with all 50 queries in view. Caught
before any labelling.

*Resolution.* The corpus was redrafted by a fresh session given `brief.md` and nothing else, under
instructions never to read the queries, the pool, or any summary of them. The pool was rebuilt. Both
corpora are retained in the repository.

*Measured effect.* Lexical echo between query terms and pooled candidates, 42 answerable queries:

| Measure | First corpus | Blind corpus | Difference (95% CI) |
|---|---|---|---|
| Best single item's coverage of the query's terms | 0.835 | 0.639 | +0.197 (0.132–0.269) |
| Query term pairs adjacent in a pooled candidate | 0.055 | 0.037 | +0.018 (0.008–0.028) |
| Query terms present in a pooled candidate | 0.175 | 0.158 | +0.017 (−0.002–0.035) |

The reversed order made a clear difference on the one measure no retriever touches. The blind corpus
is not proven echo-free: 0.639 is not zero, and the shared brief explains some of it. The two corpora
also differ in session and style, and this is one comparison on 42 queries.

*The concrete case.* At least 3 of 42 answerable queries had dedicated supporting items in the first
corpus and none in the blind one. **Q27** ("what can we say about plant protein and recovery") is the
strong instance: the first corpus held an evidence file, a permitted-wording note and a record of a
rejected post — three artifacts that exist only because a query asked about recovery. **Q13 and Q42**
(voice and formality) are ambiguous: either the first corpus invented them, or the blind corpus
omitted voice material a real brand memory would hold. The two readings cannot be separated from the
available evidence, and the log does not claim otherwise.

*Blinding evidence.* The subagent's transcript was not retained, so its report of having read only
the brief cannot be audited directly. The echo measurement is stronger evidence than a transcript
would have been: a session that had read the queries would be expected near 0.835; this one landed
at 0.639.

### 4.3 Queries were drafted with assistance and reviewed — **BUILT, frozen**
**Fixed 2026-10-25.**

The 50 queries were drafted with AI assistance from `brief.md` alone — the corpus did not exist when
they were written — and were read and accepted without changes by the project owner on 2026-10-25.

*Stated as a limitation, not a footnote.* The effect on retrieval scores is unknown in direction.
The frozen set is the assisted draft. Which queries were changed was not recorded because none were.

*Design within the set.* Eight of the 50 are **unanswerable by construction** — prices, campaign
dates, spend, conversion figures, sales by city, or brands outside the three. Without them,
precision would be measured only on answerable queries, and a retriever that always returns ten
items would look competent.

### 4.4 A third query category — three queries
**Recorded 2026-10-25.**

Three queries are neither answerable nor unanswerable-by-design, and they are not equally
diagnostic:

- **Q27** — a reasonable question the brand's own standing knowledge does not answer. Expected
  behaviour matches the eight: return nothing, or only items below the reuse band.
- **Q13 and Q42** — partly unanswerable, for a reason that is not known. General voice items in the
  blind corpus bear on part of what they ask, so the expected behaviour is those items only.

### 4.5 Precision@10 is not reported
**Decided 2026-10-25.**

The relevance protocol was specified and built in full. The labelling sitting was attempted and its
export failed validity checks: 99.8% of 1,005 judgments were a single value, 120 candidates across
five verified-unanswerable queries were marked relevant, and the median interval between judgments
was 0.89 seconds. The export was discarded and no figure computed from it.

The Phase 4 exit criterion "Precision@10 measured" is recorded **not met** — not ticked, not
provisional.

*Enforcement added afterwards.* `freeze_relevance_labels` now refuses, with no override flag: any
"relevant" under a designed-unanswerable query; any single label value above 90% of judgments; a
median inter-judgment interval under 2 seconds.

*Stated plainly, because it is the one place in this document where a threshold followed data:* the
90% and 2-second values were chosen **after** seeing the discarded export's 99.8% and 0.89 s. They
are judgement limits, not measured ones. They were fixed before any relabelling is reattempted and
must not be tuned to admit a particular export. The unanswerable-query rule is not of this kind: it
follows from a fact established independently, namely that nothing in the corpus answers those eight.

---

## 5. Source evidence

### 5.1 Authority and independence are separate axes — **OPEN** (V6)
**Ruled 2026-10-26.** The project's clearest conceptual finding; the fix is not built.

The source scorer has one axis where it needs two. A brand's own domain is **high authority** — it
knows its product's dimensions, materials and ingredients, often uniquely — and **zero
independence** about claims concerning that product's performance. The single-axis model scores it
as an unknown host (0.50), above forum posts (0.25): wrong in both directions at once.

*The rule.* A first-party source is acceptable evidence for a **factual property** of the brand's own
product. It is **not** acceptable as the **sole** evidence for a **comparative or efficacy** claim.

*Why not simply exclude first-party sources.* For "the kadai is 3 mm thick", the brand's own site is
the correct evidence and nobody else holds it.

*Why the rule is mechanically testable.* A claim that trips the superlative list is comparative by
construction; a claim in a regulated sector carries efficacy weight by construction. Both tests exist.

*Live gap, stated.* `product_claims.evidence_url` has no host check, and nothing in the schema
records a brand's domain. A claim verified against the brand's own site is today unmarked and
indistinguishable from independent evidence. The fix needs new data, so it is not a scoring tweak.
Phase 5 carries an unpopulated `first_party` marker on every search result so the pipeline need not
be reopened; Phase 6 adds the domain field, the rule, and an independence factor separate from
authority.

### 5.2 robots.txt is honoured — **BUILT**
**Ruled 2026-10-26.**

An RFC 9309 matcher was written rather than using the standard library's, which applies the first
matching rule where the RFC specifies longest-match — demonstrated by a test. Each origin's file is
fetched once and cached for a day. A 4xx means everything is allowed; a server error is not treated
as permission. Crawl-delay is not implemented.

### 5.3 PDFs are explicitly unsupported — **BUILT**
**Ruled 2026-10-26.** A PDF URL is skipped before any request, with a named error kind, rather than
failing opaquely or extracting garbage. The share of PDF results is to be measured from the search
log by extension first and HEAD requests on a sample of the remainder, before deciding whether to
support them.

---

## 6. Infrastructure constraints as design forces

These are not incidental. On a zero-budget deployment the infrastructure determined parts of the
experimental design, and saying so is more honest than presenting the design as purely
methodological.

| Constraint | Source of the figure | Design consequence |
|---|---|---|
| Strong model tier: 20 requests/day | Read from the project's own AI Studio account, 2026-10-14 | Evaluation data collection is wall-clock bound, not work bound |
| One free search engine (brave) throttles this IP hard; seven others sustained 2-minute gaps for 91 minutes | Measured 2026-10-26 to 2026-10-28 (below) | The engine set is declared (four engines), brave is enabled but undeclared; the recorder stays adaptive |
| Laptop memory: ~0.8–1.1 GB spare with a run in flight | Measured 2026-10-27 | Recording must not load the encoder (1.03 GB resident); overnight jobs can be killed, so recording must be resumable; a long search job does not share the machine with builds (`Architecture.md` §11) |
| Fetching the pages of one search: 24 s at concurrency 8, about 3.5 minutes sequential | Measured 2026-10-28, one search | Fetch is not the binding constraint; the per-query page cap is the planner's, and still unset |
| No free background-worker hosting | Provider documentation | The laptop is the worker host; public deployment deferred |

### 6.1 Search rate, as measured

**Brave alone (2026-10-26 to 27) never gave a rate.** Nine searches at 10-minute gaps over 90 minutes were
all answered. A ramp broke on the seventh search at 4-minute gaps, recovered for one search, then
relapsed. A sliding-window reading and a token-bucket fit were each proposed, registered as predictions,
and each falsified within the hour. A hold at 6 searches an hour broke at the ninth search, 1.3 hours in.
A silent wait (nobody asking) recovered between 60 and 90 minutes, and the confirming probe 10 minutes
later was refused. No constant model fits these runs and **no sustainable brave rate was established**.

**Brave was the outlier (2026-10-28).** The engine set was widened to 22 and an 8-search burst at 2-minute
gaps was run. Seven engines answered all eight searches (yep, yandex, yahoo, privacywall, duckduckgo web,
swisscows, mojeek); brave answered one search after about 8.5 hours of silence and refused every fresh
request after it. Failure counts at gaps under 3 minutes need care: SearXNG itself suspends a failed engine
for 180 seconds (3600 for a CAPTCHA) and reports it as `Suspended`, so brave's eight failures were four
refusals and four suspensions. The earlier brave logs used gaps above 180 seconds and are not affected.

**The seven-engine hold stopped at 91 minutes and is not rerun.** The first attempt (stop at the first
failure of any engine) ended at its second search on a yep HTTP 503, which is a service fault, not a
throttle. The second (run to completion: a single failure logs, three in a row mean the engine has fallen)
made **45 searches over 91 minutes at 2-minute gaps with no rate refusal from any of the seven**; its
failures were two timeouts (swisscows, privacywall) and two silent empties (mojeek); no engine fell. It was
killed by the host's memory-pressure reaper, not by an engine. **What the 45 searches establish:** these
seven sustained 2-minute gaps for 91 minutes. **What they do not:** anything about 4 hours or 120
searches, and nothing about a daily or hourly budget. The owner decided against a rerun: the recording run
is the actual workload and is a better measurement than the hold, so its first session measures the
sustainable rate while doing the work.

**The declared engine set (owner, 2026-10-28): yep, yandex, yahoo, duckduckgo web.** These four answered with
rows in all 45 searches with no failure of any kind. privacywall, swisscows, mojeek, brave and the rest stay
enabled and are not declared. The reason is arithmetic about retries: a declared engine's failure degrades a
search, a degraded search is never recorded, so every flaky declared engine multiplies retries during
recording; declaring all seven would have degraded 4 of the 45 searches (about one in eleven) on transient faults
alone, for engines whose results arrive anyway when they work. Two conditions travel with the declaration:
each search records how many undeclared engines contributed rows and which (otherwise an undeclared engine
failing silently would make a search look clean with thinner evidence, §7.2 again), and every result reports
both numbers, the **four declared engines** and the **count that actually contributed**. "Four engines" alone
would understate the evidence base and "fourteen" would overstate it. **Zero failures is a bound, not a zero.** The four declared engines showed no failure in 45 searches, which by
the rule of three puts a 95% upper bound of about 6.5% on the per-search failure rate of any one of them
(3/45 = 6.7%; exact 6.4%). If their failures are independent and each sits at the bound, up to about a
quarter of searches (23 to 24%) could degrade; if they failed together, about 6.5%. The expectation for the
recording is therefore a range, from about none to about a quarter, and not "no degradation expected". The
adaptive recorder absorbs it (a degraded search is retried, never recorded), so a high rate costs time and
not correctness; the first recording session tracks the actual degraded-search rate and reports it. 45
searches is a bound; the session is the measurement.

Which of the contributing engines share
an upstream index is **not established**: the only evidence is URL containment in one search, and the image
documents a backend for only two engines.

**Fetch cost (2026-10-28, one search).** The 79 distinct URLs of one real search on 61 hosts took 24 seconds
to fetch at concurrency 8 and about 3.5 minutes sequentially, with the per-host politeness delay of one
second included: 67 pages fetched, 12 PDFs skipped unrequested (15%), no failures. An earlier estimate of
about 26 minutes was the timeout ceiling (77 pages at 20 seconds each), not a cost, and is withdrawn. Fetch is
not the binding constraint; the cap on pages fetched per query belongs to the query planner and is unset.

**Recording arithmetic.** The planner's 8 queries per request give 240 query slots over the 30 requests and
**153 distinct searches** (identical queries record once; the key includes the declared engine set, so the
figure holds for one declared set). At 2-minute gaps that is about 5 hours, which is an expectation and not
a measurement: the first recording session measures it.

*Design consequence.* Because no single constant described brave and the seven engines were observed for
only 91 minutes, the recorder is adaptive rather than scheduled: it backs off on refusal, resumes when
clean, and records its achieved rate. The measured rate sizes the number of sessions to budget, which is
all a planning figure needs to do.

*Recorded as a methodological cost.* If the sustainable rate forces the per-request query count down, that
is infrastructure determining evidence breadth, and the thesis says so rather than presenting the smaller
number as a design choice. Likewise, the declared set is four engines because of how flaky the others were
on one day, and a different day might have declared a different four.

---

## 7. Defects found, and what each would have produced

Each was caught before it generated a number anyone would have believed. They are recorded with
their mechanism because what they would have produced is more instructive than the fix.

### 7.1 Citation does not imply entailment (V4) — **OPEN**
**Found 2026-10-18, in a real model call.**

The claim classifier cited a verified claim of "60 hours" to support the statement "Lasts all week."
The claim gate passed it, correctly by its own rules: the gate resolves whether a cited claim
exists, belongs to the product and is verified. It never checks that the claim **entails** the
statement citing it.

*Status.* A stated limitation rather than a closed defect, because entailment needs a detector that
does not exist, not a check that was forgotten.

*Partial mitigation.* When a cited claim contains a quantity or duration and the citing statement
does not repeat it, the statement is flagged for the reviewer. Numeric overstatement only — nothing
for "clinically shown" cited to one small study, or "loved by thousands" cited to forty reviews.

*Rejected.* A model-based entailment check would be one call per statement on the highest-volume
path, against a 20-per-day tier, and the gate would depend on the same model class it exists to
constrain.

### 7.2 A throttled search read as an absence of evidence — **CLOSED**
**Found and fixed 2026-10-26.**

SearXNG returns HTTP 200 with zero results when its upstream engines throttle, naming the failed
engines in a separate field. The search provider reported this as "no hits."

*What it would have produced.* A topic whose results were all lost to throttling would have been
indistinguishable from a topic for which the search genuinely found nothing. Downstream, that
reaches the gates as an absence of evidence rather than as an infrastructure failure.

*Fix.* The provider now raises a rate-limit error when a failure reason says "too many requests",
and an unavailability error otherwise. Partial engine failure is carried through to the caller and
the run report, so a run that saw one engine is distinguishable from one that saw three. The
declared engine set is part of the cache key, so entries from different configurations cannot be
confused.

### 7.3 A stored score that nothing could read — **CLOSED**
**Found and fixed 2026-10-26.**

Migration 0028 wrote `memory_items.source_score`. The retrieval function returned every other column
but not that one, so the application's `source_score` was always null regardless of the database.

*What it would have produced.* Because a missing component is left out with weights renormalised
(§2.6), the omission was silent by construction. Once sources were populated, E would still never
have arrived; every M-score would have remained four-component while appearing complete.

*Fix.* Migration 0031 returns the column. A test ties the returned column names to the receiving
dataclass's fields, verified by renaming each side in turn, so the two cannot drift apart again.

### 7.4 The checkpointer's own setup would have put a run's state where the REST API reads — **FIXED IN CODE, NOT APPLIED**
**Found 2026-10-29, by reading what `AsyncPostgresSaver.setup()` does before running it; the migration (0034) is approved and waits for the apply procedure.**

LangGraph's Postgres checkpointer creates its own tables (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`) when `setup()` is called, at run time, in the connection's
default schema. On Supabase that schema is `public`, which PostgREST exposes. A checkpoint is the run's
whole state at a step: the product, the brief, the claims in play, the generated copy.

*What it would have produced.* Four tables the migrations never mentioned, created by whichever process
called `setup()` first, in `public`, with row-level security off (the library's DDL does not enable it).
This repository's own record shows what a new `public` table carries on this platform: 0029 and 0030 had
to revoke `anon` and `authenticated` writes that Supabase's defaults had granted on `memory_items`. So the
expectation, not a demonstrated read, is that another organisation's saved campaign state could have been
read through the REST API. `setup()` was never run against the project (it would create the tables in
`public`, the thing being prevented).

*Verified 2026-10-29 on an equivalent Postgres, and what that does and does not show.* `setup()` was run
against a scratch database on the test server (pgvector/pg17, with the platform stub applied). It chose
**`public`** (the default `search_path` is `"$user", public`, and the library's DDL names no schema) and left
**row-level security off** on all four tables. Both are the library's behaviour, independent of the
platform. The grants are NOT independent evidence there: `anon` and `authenticated` could `SELECT` and
`INSERT`, but only because `tests/db/supabase_platform_stub.sql` declares Supabase's default privileges by
hand. The platform's side was read from the live project instead, in the rolled-back dry runs that
changed nothing: a column added to `public.runs` (0035) came back granted `INSERT`, `UPDATE`, `SELECT` and
`REFERENCES` to `anon`, `authenticated` and `service_role`, and a new table in `public` (0036) kept
`SELECT`, `REFERENCES` and `TRIGGER` for `anon`, `authenticated` and `service_role` after four privileges
had been revoked, so the defaults were at least that. What was still inference, and the project's own evidence for it (owner, 2026-10-29): that this project
serves `public` through PostgREST to `anon` and `authenticated`. The design itself rests on it. The
whole row-level-security posture, 0003 to 0030, exists because those roles can reach `public`: thirteen
migrations create policies, 0010 and 0011 revoke delete and truncate from `anon` and `authenticated`,
0013 revokes their writes on `llm_calls`, and 0029 and 0030 revoke `anon`'s and `authenticated`'s
table-level `INSERT` on `memory_items`, stating that since 0006 they "held table-level INSERT and
UPDATE", blocked "only by row-level security … not by the grant". That is the project recording, from
its own tables, both that default grants arrive on a new `public` table and that row-level security is
the only barrier. If `public` were not exposed, that work would be unnecessary. This is evidence from
the project, not proof (nothing here sent a request through the REST API), and it supports the same
conclusion as the three demonstrated facts: a table the library created in `public` with RLS off would
have been readable by `anon` and `authenticated`.

*Fix.* Migration 0034 creates the library's own DDL (versions 0 to 9, compared with it column by column by
a test, so a library upgrade fails a test instead of writing to tables that differ) in a private schema,
`graph_checkpoints`, with row-level security on and every API role revoked. The backend connects as the
owning role, sets its `search_path` to the schema and never calls `setup()`; `assert_checkpoint_schema`
refuses to start if the installed library expects a newer version than the migration made. Tests: the
API roles get `InsufficientPrivilege` on all four tables, and row-level security is on for each
(`tests/db/test_graph_checkpoints.py`).

### 7.5 A session's backup ended not-ok every time, and its certificate went stale — **CLOSED**
**Found 2026-10-29 in recording session 4's report; fixed the same day.**

The backup tool treated every file other than the three index files as write-once. The same-disk waiver
log (`backup-waivers.jsonl`) is append-only by design: a line is added at every launch that uses the
override. So the second launch made the store's copy longer than the backup's, the tool called that a
difference, refused to copy, and reported `ok: False`; `BACKUP-VERIFIED.json` was never rewritten.

*What it would have produced.* A store of 25 requests certified as a store of 12. The data files were
byte-identical throughout, so nothing was at risk, but the certificate was the record that said so, and a
reader of it would have believed the backup was 13 requests behind.

*Fix.* The waiver log is held to the index files' rule (owner, 2026-10-29): the backup's copy must be a
byte prefix of the store's, which allows growth and catches truncation and divergence. Tests: the log
grows across three backups and each is ok; a backup longer than the store's, and one with an earlier line
rewritten, are each refused and left as found. The real backup was re-run and certified (25 requests, 129
searches, 4,352 pages, 3,377 files).

### 7.6 A re-executed step double counted, and nothing could tell a doubled call from a retry — **FIXED IN CODE, NOT APPLIED**
**Found 2026-10-29 while building the graph; the migration (0035) is written and dry-run, and waits for the apply procedure.**

The checkpointer is at-least-once, so a step whose worker died is run again, and a model call made by it
is made again. `llm_calls` had no column saying which step, revision round or execution made a row.

*What it would have produced.* Every per-campaign effort figure (calls, tokens, cost) inflated for exactly
the runs that were resumed, with no way to correct it afterwards: a re-execution, a revision round and the
gateway's repair call looked alike. The arms are compared on cost and calls, so a worker restart during an
arm's run would have moved a result. Measured: a worker killed inside one of three single-call agents left
4 rows for 3 calls, and `runs.llm_call_count` read 4.

*Fix.* The graph stays at-least-once. Migration 0035 records `step`, `revision_round` and `execution_id`
on every graph call; the attempt is derived, and the evaluation reads spend (every row) and work (the last
attempt) separately, with the difference reported (`docs/evaluation-plan.md`, "Re-executed steps").
Recorded before any real run, because afterwards it cannot be reconstructed.

### 7.7 A run that completed with a failed agent was the same row as a whole one — **FIXED IN CODE, NOT APPLIED**
**Found 2026-10-29 by asking whether the run record, not only the event stream, tells a partial run from a
complete one.** A budget stop was told apart (`partial`, `budget_exhausted`); a declared gap was not. 0035
adds `runs.declared_gaps`, written with the terminal status, and the per-arm report counts them.

### 7.8 A finished graph whose run was never ended stayed `queued` for ever — **CLOSED**
**Found 2026-10-29 while auditing re-execution.** A worker killed after the last node and before the
status write left a finished checkpoint and a `queued` run; `recover` refused it. `recover` now ends it from
the checkpointed state (completed or blocked), without committing the dead process's staged memory.

### 7.9 Other closed defects
- A password leaking into a settings validation error.
- The M-score's `C` and `E` could not enter any M because `score()` had no caller. Found 2026-10-25, closed
  2026-10-29 by `app/memory/recall.py` and 0037; the four-component M is gone as a limitation of the code,
  and remains a property of any item that lacks persona context or a source score (see `Memory.md` 3.2).
- `check_copy` turned a `BudgetExceeded` into a `gate_error` block, so a run out of budget at step 11
  would have shown the approver a "blocked" draft instead of ending `partial` (Rules.md C4). It now
  re-raises. Found 2026-10-29 wiring step 11; no earlier test pinned the old behaviour.
- Step 15's refused-candidates audit row was written once per execution, so a re-run step would have
  written it twice (audit rows cannot be deleted). Now one per run. Found 2026-10-29 in the re-execution audit.
- `Architecture.md` §4.4 said `runs.status` had no database CHECK; it had one since 0015. Found 2026-10-29
  while writing 0033 against what was actually there; the sentence was corrected first, then the migration written.
- `service_role` able to hard-delete `memory_items`, `runs` and `llm_calls` — revoked in 0010–0011.
- Signed-out users able to execute `app.create_organisation` — caught only by a rolled-back dry run
  against the live project, which the CI stub could not model (fixed in 0012).
- An E2E fixture brand created three times, because a helper counted before the list loaded. The
  duplicates were deleted; their two sector-audit rows remain permanently as the trail.
- 80 characters of every refused write-back candidate copied into the log, including candidates
  refused **for containing an email or phone number**.
- The developer's email address placed in the fetcher's User-Agent, which is sent to third-party
  servers. Caught before commit; a test now asserts no personal address appears in the header.
- Three occurrences of `\b` and `\n` mangled into control characters by edit scripts; a UTF-8 BOM
  prepended to a migration file by PowerShell, which errored the suite rather than failing it.

*Hazards identified during development, never committed in defective form:* Postgres's `least()`
ignoring nulls, which would have set a reinforced item with no confidence to the maximum. The guard
is present in the first commit of that code.

---

## 8. Stated limitations

| | Limitation | Status |
|---|---|---|
| V1 | Strong-tier asymmetry between arms | Stated |
| V2 | Paraphrase gap: whether a generator's paraphrase of a restricted claim is mislabelled | Deferred to Phase 6, where the generator exists and the threat is end-to-end |
| V3 | Anti-patterns are checked by the human approver, **assisted since 2026-10-29 by a model advisory (A7's risk read) whose accuracy is unmeasured**; the approver's miss rate is still unmeasured | Stated; T5 recorded unmet. The advisory does not change either: it is an opinion, not a detector, and having it measures nothing |
| V4 | Citation does not imply entailment. **Status changed 2026-10-30: V4 is now LOAD-BEARING, no longer only a stated limitation** (see V12): the revision loop's apparent success depends on it. Observed on the exact V11 copy: the same attribute ("lightweight walking shoe") was reported with no claim in four judgments and cited to "Lace-up, low top, medium width, almond toe, low arch" in a fifth, and the gate passed it. The classifier call sets no temperature (provider default), so identical copy can get different verdicts | Open. Partial mitigations, none of them entailment: numeric (`unrepeated_quantity`, flag) and lexical (`citation_lacks_term`, block, V12). A model-based entailment check is still refused (volume, and a 20-a-day tier). A deterministic setting for the classifier is proposed, not made |
| V5 | The source authority prior is the developer's untuned judgement | Mitigated by reporting results with the prior flattened |
| V6 | The source model conflates authority with independence | **Built (Phase 6 chunk D, 2026-10-31); 0039 and 0040 applied; the sole-evidence rule BINDS NOTHING in this evaluation.** `brands.domains` (audited), `first_party` on every finding (None when no domain is registered), the host check on a verified claim's `evidence_url`, independence kept with a memory item's evidence (per-source flag, derived item flag, returned by `match_memory`, not an M input). The evaluation brands' domains are registered, so marking is complete (128 of 128). The claim gate blocks a statement citing a verified claim whose one evidence URL is the brand's own page when the claim is comparative (trips the superlative list); **0 of 128 do**. The regulated (efficacy) trigger was dropped (owner, 2026-10-31): it conflated the sector with the claim and blocked 12 of 12 of B07's legitimate composition claims. **Gap left:** an effect claim mislabelled `verified` rests on the brand's own page unchecked; the principled fix, the claim's nature as an audited field (option c), is deferred | Built; (c) deferred; memory path see V13 |
| V7 | The evaluation's searches can only return evidence about a product's category, never the product (pseudonyms); facet 3 cannot verify a specific product's claims | Stated 2026-10-28; bounds what the evaluation can show about claim grounding |
| V8 | The frozen snapshot has no off-device copy: its only backup is a folder on the same physical disk, so reproducibility depends on one disk | Stated 2026-10-28; the zero-retention exception's retention condition is partially met; the same-disk override is deliberate and logged |
| V9 | The declared engine set was chosen from one clean 91-minute window; yahoo has a code defect in the pinned image and yep returns 503 in spells, so the set is effectively two reliable engines and two that are not | Stated 2026-10-29; the three-of-four rule needs only one of the unreliable pair and fails when both go down (`docs/evaluation-plan.md` section 8) |
| V10 | A2 hands a model third-party web pages, so a page can try to instruct it (prompt injection), and A2's output now influences the copy. **The split (owner, 2026-10-30): A6 receives A2's DERIVED output only (`for_copy`: category norms and whitespace as short summaries the model wrote and code checked for markup, URLs and any run of five words copied from a page), never a verbatim quote, competitor message, source URL or page excerpt.** A quote can be genuinely on a page and still be a payload, so checking it against the source proves provenance, not safety; quotes stay in state for the approver's view only. Also: pages sit in a wrapper they cannot close, the prompt calls them data, the output is a closed schema nothing executes. **What this does NOT cover:** a derived line is still written by a model that read hostile text, so an injection that survives summarisation survives this; the five-word copy check bounds a pasted payload, not a short or reworded one (and five is an untuned judgement). Resistance to a real adversarial page has NOT been tested, only synthetic wrapper-escape text | Stated 2026-10-30; the first real run of A2 reads recorded pages that were not chosen to attack it |
| V11 | **A claim-worthy assertion that is never extracted is judged by no gate.** Found 2026-10-30 by a real run (request R10): the brief's objective text says "lightweight walking shoe"; A1 carried it into the positioning, A6 wrote "Make your commute easier with a lightweight walking shoe", no claim says the shoe is light, and `claim_extract@v1` extracted four statements and none for it (the attribute sits inside a noun phrase, not a sentence-level assertion), so the copy PASSED. Not V4 (a statement judged wrongly): here the statement is not judged at all. **The leak was explicit:** `a1_positioning@v1` said the positioning was "built only from the listed claims and the brief"; the prompt permitted it and nobody noticed until a real run put the word in a headline. **Closed three ways (owner, 2026-10-30):** (1) `a1_positioning@v2`: the brief gives the objective and audience, never product properties; (2) the brief is classified once per REQUEST and flagged and recorded, never blocked (`docs/evaluation-plan.md`, "The brief check"); (3) `claim_extract@v2` extracts property assertions inside noun phrases (no word list: T5), and the reviser is told the offending span for `unsourced_claim` only. **Live, 2026-10-30, n = 1 request (R10), no rate:** the brief check flagged "lightweight walking shoe" as unbacked; A1 v2's positioning and A6's first draft (the brief still saying lightweight) contained no "lightweight"; the classifier extracted eight statements, every one cited to a claim that mentions it. Earlier, before (1): the span did not make the reviser remove the word (run X), see V12. **Not closed:** one sample per mechanism; v2's recall on other attributes is unmeasured; the classifier is nondeterministic (the same brief-check call gave 48 and 28 output tokens on near-identical input); a model that finds nothing passes the copy as before. **The brief check across all 30 requests (2026-10-31, precomputed once at temperature 0): 1 of 30 briefs carries an unbacked property, R10's "lightweight walking shoe" (3.3%; Wilson 95% interval 0.6% to 16.7%).** Read by eye, the 30 objectives agree; the extractor's recall is still unmeasured. **Rulings (owner, 2026-10-31).** The brief check STAYS a flag, because blocking at admission is redundant: three defences already stand between an unbacked brief attribute and the copy (A1 v2 declining to take product properties from the brief, the classifier extracting attribute assertions, and the gate blocking unbacked ones), and a fourth would cost a whole request across all three arms to add nothing they do not already do; at 29 of 30 clean (interval above) blocking was considered and rejected as redundant. Each of the three is itself partial and observed on one request, so this rests on their combination. **R10's brief stays UNCHANGED:** editing the test set to remove an attribute that proved inconvenient, after it produced a finding, is the post-hoc adjustment that pre-registration exists to prevent; considered and refused for that reason | Mitigated in one sample, not closed; recall of v2 to be measured on approver-labelled copy |
| V12 | **Under a revision loop an unsourced-claim finding is satisfiable by citing any active claim, because the gate verifies that a citation resolves, not that it entails.** The model is not deceiving anyone; it is optimising against the check it was given. Exact instance (R10, 2026-10-30, run X): "lightweight walking shoe" was reported with no claim on draft 1; the reviser, told the span, kept it in draft 2; the classifier then cited it to "Lace-up, low top, medium width, almond toe, low arch"; the gate passed it. **This makes V4 load-bearing:** the loop's apparent success depended on it. **Mitigation: `citation_lacks_term`** (`app/core/citation_terms.py`, a block, no model call, no property word list): the cited claim's own text must contain what the statement asserts, after function words and the words that name the product are removed; a short statement (3 terms or fewer) needs every term in the claim, a longer one at least one. It blocks the exact draft 2 and passes its eight other statements. **It is a word-overlap test, not entailment, and it does NOT catch:** (a) paraphrase and synonymy, in both directions: a claim saying "light" does not contain "lightweight", so a correct citation can be BLOCKED. Two existing V4 tests now block, and they are not the same: "Lasts all week" cited to "Up to 60 hours playback (at 60% volume)" is V4's original example and a TRUE block (a week is 7 days, 60 hours is 2.5; the statement overstates the claim), so the check catching it is the mitigation working, though it is caught for a lexical reason that happens to coincide with the real one; "We think it lasts for years", a labelled inference from the deduced claim "About 300 charge cycles", is the one observed block that is arguably WRONG, because the design lets a labelled deduction through as a flag and the check blocks any inference worded unlike its claim (ruled 2026-10-31: a labelled deduction is now EXEMPT from this check, see below). No correct paraphrase wrongly blocked has been seen in real copy; "light" against "lightweight" is a constructed unit test. The false-block rate is unmeasured and its measurement is PRE-REGISTERED (`docs/evaluation-plan.md`); (b) a property word the claim negates ("not lightweight") or uses in another sense, which passes; (c) in a long statement one shared incidental word is enough, so a mis-cited sentence that also names a real feature passes; (d) anything the extractor never extracts (V11). **Since 2026-10-31** the reviser is shown the span of this finding, as for `unsourced_claim` (the span only, never the message or the draft's other text), so re-citing to another claim now fails unless that claim's own text contains the term. **Exemption (owner, 2026-10-31): a labelled deduction (a statement the copy presents as an inference, citing a DEDUCED claim) is exempt from `citation_lacks_term`.** A deduction restates its basis in different words, which is what inference is, so a lexical overlap test is structurally wrong for it; deductions already flag for the reviewer rather than pass silently, which is the right handling for what the system cannot verify. **Condition: the `deduced_labelled` flag states that the deduction's basis was NOT lexically checked**, so an approver does not read the flag as "checked and passed". Not an escape through the label: the exemption applies only to a citation of a deduced claim; a statement labelled an inference that cites a VERIFIED claim is still checked, and an unlabelled deduction is still the old block. Its cost: a property asserted as an inference from a deduced claim ("we think it is lightweight") is flagged and not blocked, as a labelled deduction always was. **Live, 2026-10-31 (run z, one request, n = 1):** the gate tripped `citation_lacks_term` on the very copy that passed on 2026-10-30 (the classifier's verdict on it, replayed), the span was shown to the reviser, and draft 2 dropped "lightweight" and passed with every one of the classifier's nine statements cited to a claim that mentions it. The audit log kept the block with the statement and the claim's text. It is one sample, not a rate; the reviser obeyed here and did not in run X, but the finding kind, the instruction wording and the classifier's temperature all differ between the two, so the change cannot be attributed to the span alone | Stated 2026-10-30; the false-block rate to be counted on the 30 requests' copy before the check's severity is relied on |
| V13 | **The Proposed arm's memory could not accumulate.** Found 2026-10-31 by checking whether the six-component M exists in practice. **Campaigns 2 and 3 of a brand saw nothing from campaign 1, so until extraction exists the Proposed arm is a no-memory arm and no M has ever been computed in a real run.** `writeback_step` passed `candidates=[]`: nothing extracted facts from an approved campaign, so a real run wrote no memory item (the thesis's memory contrast was, as built, a contrast between identical conditions). Separately, write-back recorded no evidence sources, so even a written item had a NULL `source_score` and `E` left out; chunk C's guard inserted its rows by hand. **Mitigation (2026-10-31):** (1) `commit_memory` records scored evidence sources with `first_party`; (2) a DETERMINISTIC proposer (`app/memory/proposer.py`, owner ruling: a model proposer would add a call per run, a failure mode and nondeterminism to the one mechanism the thesis is about) offers A2's quote-verified category conventions (`insight`) and competitor messages (`competitor`), each with the scored page it came from, so each item carries the full component set; (3) rule 2 (Memory.md 6.1, "deduced with a scored source") now accepts a scored source for those two kinds and no others; a test shows campaign 2 recalling campaign 1's items with all six components through the real path. **Item kinds created: only those that can carry the full component set.** Claim items are NOT created (their evidence page is not scored, so they would lack E while insights have it, and one reuse band would meet two scores that mean different things, invisibly; the claim already lives in `product_claims`); persona items are not (personas travel as persona rows on every item); A1's positioning is not (its basis, verified claims, has no scored page: a ruling on a `strategy` kind or on scoring the claims' pages is pending); whitespace never (an inference with no verified quote, Memory.md 6.2). **Not mitigated:** the initial confidence (0.60) and the typical freshness are stated defaults, not tuned; with only two kinds written, `memory_coverage` over the placeholder six-kind `required_slots` cannot exceed 2 of 6, so the research-necessity step will rarely see memory as sufficient until that list is ruled; and the M-score, the bands and chunk G still rest on a mechanism exercised only by the tests above, not yet by a full real run | Mitigated 2026-10-31; owner rulings pending on A1/strategy and on `required_slots` |
| V14 | **Nothing reads memory.** Found 2026-10-31 while answering what the reuse bands act on. The bands decide what to do with a recalled item (inject verbatim, inject with adaptation, background only, discard) and define coverage, which is meant to set the research depth. As built: the Research Necessity Engine is a placeholder (`assess_research_necessity` uses the depth the request asked for and records `R = 1 - coverage` only); no agent receives `memory_hits`; A2 is skipped only when the REQUEST's depth is D0. So with any kinds, a Proposed run behaves like a no-memory run, and the memory contrast (ARR, SRR, CR, LR) is zero by construction until something consumes it. **Scheduled:** the engine (memory to depth) and dynamic agent selection are Phase 7 (exit: "identical request twice: second run picks a lower depth, logs prove it"). **Not scheduled in any phase I can find:** injecting recalled knowledge into the agents (what the 0.90 and 0.80 bands literally describe). Memory.md 3.2's reuse bands act on knowledge slots of any kind; the designed cold-start progression (section 7) and every cost metric (ARR, SRR, CR, LR) are about research depth, not about reusing prior copy, so a two-kind RESEARCH memory supports the cost-and-speed story in principle and does not support a copy-reuse story at all (creative items cannot be written) | Stated 2026-10-31. **Ruled 2026-10-31 (owner): injection is scheduled in Phase 7 with the engine, and the evaluation cannot run until it lands (2.10).** Not built |
| V15 | **No revalidation of memory sources.** Nothing re-fetches an item's source URL, so a verified-claim item's exemption from decay ("until its source URL fails revalidation", Memory 3.3) and the "scheduled revalidation" staleness defence (Memory 9) do not operate; staleness is handled by freshness decay alone, and no `claim` item is written anyway (V13). Out of scope for the thesis: it is a product-maintenance job (a scheduler plus a fetch policy), and the evaluation runs over a frozen page snapshot where sources cannot change | Deferred 2026-10-31 |
| V16 | **No revoke control in the UI.** Revocation exists in the database and is tested (Memory 6.4); no API route or screen lets a brand manager revoke an item. Out of scope: a product concern; the evaluation revokes by rule (confidence decay) and the owner has direct database access | Deferred 2026-10-31 |
| V17 | **No `/api/v1/memory/search` route.** The M breakdown exists in `app/memory/recall.py`; nothing returns it over HTTP, so the "memory retrieving with scores" demo (Phases milestone, week 7) has no screen. Out of scope for the thesis: results are read from the run log and `llm_calls`; it is a demo convenience | Deferred 2026-10-31 |
| V18 | **The fallback tier is not routable.** `app/llm/registry.py` states `media` and `fallback` are not routable, so a provider outage or exhausted quota fails the run (or is downgraded by Phase 7) rather than moving to an OSS model. Out of scope: the zero-cost constraint leaves no hosted OSS model to route to, and evaluation runs are repeated on infrastructure failure by rule (evaluation plan section 2) | Deferred 2026-10-31 |
| V19 | **No latency budget in code.** Architecture 4.3 says a rate-limit wait that would push a run past its latency budget ends the step as a partial result; no budget exists, so a long wait just waits. Out of scope: latency is measured and reported with and without waits (Phase 6 chunk F), not enforced | Deferred 2026-10-31 |
| V20 | **Organisation rate limits are not enforced.** `rate_limit_runs_per_hour` and `rate_limit_requests_per_minute` exist as columns; only the monthly spend cap is enforced (`app/llm/ledger.py`). Out of scope: a multi-tenant product concern; the evaluation has one user and the free-tier model limits already bound throughput | Deferred 2026-10-31 |
| V21 | **The design needs two humans besides the builder, and the project has one.** Found 2026-11-01 by asking who the second calibration scorer is. **Instance 1, D3 (`docs/evaluation-plan.md`, `docs/approval-rubric.md` section 6):** before run 1 the approver scores 10 practice campaigns and "a second team member independently scores the same 10"; the rubric is amended until they agree on at least 8 of 10 (a raw count of campaigns whose two approve / revise / reject outcomes match, `calibration_agreement`, not a statistic). **Instance 2, D2 (evaluation plan section 6, `PRD.md` 7.1, `Phases.md` Phase 10):** two human raters **who did not build the agents** rate all 30 requests' outputs in every arm, blind to arm, with Cohen's kappa per rubric dimension. Neither the second scorer nor the raters are named anywhere ("not yet named"); Kundan is the only human, and directed the build (AI wrote the code), so Kundan is a doubtful candidate for either role and cannot be two raters. `Memory.md` does not mention approver calibration, but chunk G calibrates the reuse bands "against approver judgement", so it rests on a calibrated approver too. **Options (owner, 2026-11-01).** (a) **Recruit two people**: two raters, blind to arm, rating 30 campaigns against the rubric with Cohen's kappa; one of them is also the second calibration scorer on the 10 practice campaigns, so it is one recruitment problem, not two. Rough cost per person: the 10 calibration campaigns plus 30 evaluation campaigns, three to four hours over two sittings. **Recruitment is being attempted first.** (b) **One rater, no kappa, limitation stated**: the single rater directed the system's construction, so the bias direction is known and favourable; every quality figure is reported with that caveat attached. (c) **Drop the human quality rating**: considered and REJECTED, because it leaves the efficiency claims (ARR, SRR, CR, LR) with no quality control: a cheaper campaign that is worse would read as a win. **Nothing is implemented for this.** Until a ruling, the calibration and every human quality score are blocked, and chunk G's band calibration depends on the calibrated approver | Stated 2026-11-01; recruitment under way; options (a) first, (b) fallback, (c) rejected |
| V22 | **Wall-clock latency is not a fair arm comparison on this hardware.** Under replayed research, a run's wall time is dominated by LOCAL encoder time on consumer hardware (first real campaign, one sample: 297 s of active time against about 11 s of summed model latency, 2026-11-01; not yet decomposed), and the Proposed arm embeds more than either baseline (recall at steps 4 and 8, and write-back embedding), so total wall-clock LR favours the baselines for a reason unrelated to the hypothesis. The comparable figures are the model-latency and search-latency components (2.12). Wall clock is reported separately and caveated, with the four components and the signed residual beside it (migration 0044). The decomposition is built; the one-sample figure above stays unquoted as N1/N2 until it is measured in parts over several runs | Stated 2026-11-01; decomposition built, measurement pending |
| V23 | **The quality rubric's human-rated dimensions are undefined.** `PRD.md` section 7.1 names product relevance, customer relevance, brand consistency, originality and feasibility (and "trustworthiness: hallucination audit, manual") with weights and a "scored by", but gives **no definition and no scale** for any of them, and `campaigns.quality_scores` (jsonb, "blind raters only") has no documented shape. D2's "Cohen's kappa per rubric dimension" therefore cannot be computed for them as written. The approval rubric (gates, checks, outcome) is fully documented and does have categories. Found 2026-11-01 building the rater sheets (2.15). Defining each dimension and a scale is the owner's and the raters' decision, to be recorded as a rubric amendment before the calibration round (booked 8 November) | Stated 2026-11-01; resolved in draft by amendment A1 (V24), not yet frozen |
| V24 | **The quality rubric's definitions and scales are the advisor's own, not a validated instrument.** Amendment A1 (1 November 2026) defines six human-rated dimensions on a 0 / 1 / 2 scale; the anchors were authored by the project's advisor, are not derived from a published instrument, and have not been validated, so inter-rater agreement on them shows that two raters can apply THESE anchors alike, not that the anchors measure what their names say. Every quality figure from this rubric is reported with that caveat. A second limitation travels with it: the agreement round of 8 November uses 10 campaigns, which cannot estimate a kappa (it can only find ambiguous anchors) | Stated 1 November 2026; the amendment is a draft until frozen |
| V25 | **A native-code crash of the worker has been seen once, and its rate is NOT measured; a campaign that needs research may lose its worker.** Found 4 November 2026 (decision-log 2.25). *[CORRECTED the same evening. This row first said the worker crashed on 4 of 13 research executions, about 31 percent. That figure is WITHDRAWN: six of those crashes happened inside the project's own stack dumper, not in extraction.]* **What is established:** one access violation (`0xC0000005`) with no dumper armed, in lxml `etree`, at 09:38:51 IST, three seconds into a research step, before the lock existed. n = 1, so NO rate can be stated. The six later crashes that day (a worker at 14:10:04, a replay script, locked run 5, three stock-interpreter runs) all happened INSIDE `faulthandler.dump_traceback_later`, which reads other threads' frames without the GIL: in each log the line `File ` is cut off by `Windows fatal exception`. They are no evidence about trafilatura, lxml, the lock, the interpreter build or the OS. The lock has NOT been validated: every locked replay so far ran with the dumper armed. Cause of the 09:38 crash NOT established (a documented lxml parser-sharing hazard exists and is locked against). 15 passes of 50,520 regex substitutions over every recorded page did not crash the interpreter. **What it implies, with the arithmetic, IF the rate were as high as the owner's working figure of 1 crash per 5 research steps (an assumption, not an observation).** A batch then survives with probability 0.8 raised to the number of research steps: 8 live campaigns, 0.8^8 = 0.17 (about 1 batch in 6 finishes untouched); the 90-campaign evaluation, up to 90 research steps, 0.8^90 = 2e-9, i.e. crashes are CERTAIN, about 18 expected. If the true rate is far lower the arithmetic changes with it: 1 in 100 gives 0.99^90 = 0.40. Measuring the rate with the dumper gone is the next step. Baseline arms that do no research are not exposed. *A crash is not a wrong result:* the run stays `queued`, nothing is written, and the next worker start resumes it from its checkpoint, so the cost is minutes per crash and an at-least-once re-run of the step that was running. *What it does corrupt if unhandled:* nothing silently (the run is visibly unfinished and migration 0046 makes a timed-out job visible), but the wall-clock latency of any run that crashed is not a latency, and a campaign that crashes during a measured run must be flagged so it is excluded from latency figures. **The interim runs under a supervisor that restarts the worker, as a STOPGAP for the interim only, never the evaluation's fix.** The fix is process isolation of extraction (a persistent pool, retry of in-flight pages only, determinism asserted at 319 pages and 159 findings), whatever the platform diagnostics show: one bad page must not kill a worker. Also a PRODUCTION risk, not only an evaluation one | Stated 4 November 2026; crash log `C:\BrandverseData\logs\stage2-crashes.jsonl`; closes when extraction is isolated and the rate is measured again |

Additional stated limitations: tests run as a database superuser, so privilege failures appearing
only for a non-superuser role are invisible to CI; accent handling differs between the lexical and
vector retrieval halves — demonstrably inert for this corpus, which contains no accented characters,
and fixable only before the pool is frozen; the audit log's guarantee is bounded by the table
owner's ability to disable the trigger, as Supabase provides no role above `postgres`.

---

## 9. Process rules adopted after failures

Each was adopted because something had already gone wrong, and each converts a remembered discipline
into an enforced one.

| Rule | Adopted after |
|---|---|
| Pre-apply checks run as the role that will apply them, on a connection with no state the real apply lacks | A migration passed a dry run because the connection had pgvector already loaded, then failed on the live project |
| No edit script touches a file containing regex or backslash escapes | Three occurrences of `\b` and `\n` mangled into control characters |
| Regex patterns reach the database as parameters, or via a migration with a test that reads the pattern back | Same |
| No credentials for the live project in CI, enforced by an allowlist test | A decision rested on a rule that did not exist |
| The full local suite runs before every push, not after CI fails | Two CI cycles spent on a long line and a missing documentation row |
| A migration is applied only after CI is green on the commit containing both the migration and its documentation row | The documentation-sync test makes a migration-only commit unable to pass |

Three guards were tested by being made to fail against the real artifact rather than only against
fixtures: the dry-run harness was re-run against the migration that had slipped through and refused
it with the live error; the CI-credentials test was proved by planting a real key expression in the
live workflow file; the recording-path import guard was proved by adding a direct import, a
transitive one, and an unlisted recorder module.

---

## 10. This document's own errors

Revision 1 was verified line by line against the repository. Seventeen discrepancies were found and
corrected in revision 2. Three are worth recording, because they are instances of the same failure
mode the project catches elsewhere:

1. **Designed rules described as enforced.** Opt-in experiment membership and the per-request window
   were written as though in force. Both are designed and recorded; neither is built. This is the
   failure of §3.1 — an outcome table claiming an enforcement that does not exist — committed in a
   document about not doing that. Revision 2 marks every entry BUILT, DESIGNED or OPEN.

2. **An unsupported claim reintroduced.** A sentence predicting that hosted model endpoints change
   without version bumps had been deliberately deleted from `Memory.md` as having no evidence behind
   it. Revision 1 restored it. Revision 2 states only what is checkable: a pinned local model records
   a revision and a hash; a hosted endpoint does not.

3. **An overbroad pre-registration claim.** Revision 1 opened with "no metric has been computed" and
   closed with "decisions made after seeing data: none." Both were too strong. Echo figures, export
   diagnostics and partial search rates have been measured, and two freeze-script thresholds were
   set after seeing a discarded export. Revision 2 states what has and has not been measured, and
   §4.5 records the one threshold-after-data case where it occurs.

Lesser corrections: two dates, the attribution of the model rate limits to the project's own account
rather than to published documentation, "bestow" miscategorised as an accepted false positive when
it is a checked-correct case, the third query category restored to three queries rather than one,
one defect downgraded to a hazard never committed in defective form, one defect's consequence stated
more strongly than the repository supports, a count of prior leaks that no document corroborates,
"transcript returned empty" corrected to "was not retained", the arms' query sets clarified since
Baseline A performs no research, the blocked status of the retrieval-depth sweep made explicit, one
guard's proof method misattributed, and two further superseded decisions marked.

*Why this section exists.* The log's value rests on it being checkable. A reader who finds one
uncorrected overstatement has reason to doubt the rest, and the cheapest defence is a record of what
was found when it was checked.
