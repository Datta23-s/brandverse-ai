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
**Recorded 2026-10-25.**

The M-score is defined over six components (S, P, C, A, F, E). C awaits persona vectors in Phase 6;
E awaits source scores. A missing component is **left out and the remaining weights renormalised**,
and the result names what was left out. No neutral default is substituted anywhere.

*Reason.* A neutral default would produce plausible M values that silently were not the M the
thesis defines.

*Consequence, recorded.* Bands of 0.90 / 0.80 / 0.65 and the write-back gate at M ≥ 0.90 are
currently applied to a four-component M. Nothing banded before E and C exist is comparable with
anything banded after. Band tuning and the ten-campaign approver calibration happen only after both
exist. No figure derived from a four-component M is reported anywhere.

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

---

## 3. Output gates and claim handling

### 3.1 Anti-patterns: specified, not implemented — **OPEN**
**Ruled 2026-10-18.**

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
had been revoked, so the defaults were at least that. Remaining unproven: that this project exposes
`public` through PostgREST (its row-level security policies on `public` tables assume it does) and that
`setup()` itself, run there, would add nothing to that. So: the schema, the missing RLS and the default
grants are demonstrated; the read through the REST API is still the inference those three support.

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
| V3 | Anti-patterns are checked only by the human approver; the miss rate is unmeasured | Stated; T5 recorded unmet |
| V4 | Citation does not imply entailment | Open; partial numeric mitigation |
| V5 | The source authority prior is the developer's untuned judgement | Mitigated by reporting results with the prior flattened |
| V6 | The source model conflates authority with independence | Ruled; fix requires a brand domain field in Phase 6 |
| V7 | The evaluation's searches can only return evidence about a product's category, never the product (pseudonyms); facet 3 cannot verify a specific product's claims | Stated 2026-10-28; bounds what the evaluation can show about claim grounding |
| V8 | The frozen snapshot has no off-device copy: its only backup is a folder on the same physical disk, so reproducibility depends on one disk | Stated 2026-10-28; the zero-retention exception's retention condition is partially met; the same-disk override is deliberate and logged |
| V9 | The declared engine set was chosen from one clean 91-minute window; yahoo has a code defect in the pinned image and yep returns 503 in spells, so the set is effectively two reliable engines and two that are not | Stated 2026-10-29; the three-of-four rule needs only one of the unreliable pair and fails when both go down (`docs/evaluation-plan.md` section 8) |

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
