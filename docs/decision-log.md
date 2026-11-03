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
