# Phase 7, first decision: where R's inputs come from

| Field | Value |
|---|---|
| Status | **Options for the owner. Nothing here is ruled and nothing is built.** |
| Date | 2026-11-01 |
| Needed by | Phase 7 build start. **Freeze plan (owner, 3 November): 11 November with 7 brands; 9 November is upside only** (section 7). Earlier text in this file that reasons "before 9 November" is the 1 November position, superseded. |
| Governs | `Architecture.md` section 6.1 (the Research Necessity Engine) |

## What the design says

```
R = wN N + wU U + wF F + wS S + wE E          weights 0.30 / 0.25 / 0.20 / 0.15 / 0.10 (config/scoring.yaml)
R < 0.35 -> D0     0.35 <= R < 0.70 -> D1     R >= 0.70 -> D2          (untuned; tuned in Phase 10)
```

| Input | The design's words (`Architecture.md` section 6.1, verbatim) | Has a source today? |
|---|---|---|
| `N` | "Novelty of request vs memory", from "1 - max cosine similarity to prior runs" | **No** |
| `U` | "Uncertainty", from "fraction of required knowledge slots unfilled" | Yes: `1 - memory_coverage`, in k of 2 slots, so `U` is 0, 0.5 or 1 |
| `F` | "Freshness requirement", from "request type: pricing/competitor = high, brand voice = low" | **No** |
| `S` | "Sector risk", from "regulated sectors (health, finance) score high" | Partly: `sectors.regulated` is a boolean, so `S` is 0 or 1; "score high" gives no other value |
| `E` | "Evidence requirement", from "claims needing external proof" | **No** |

**Facts that decide the options.** (1) The system's request is a free-text `brief`, an `audience`, a `depth` and a product: there is
no request type. (2) All 30 evaluation requests are the same kind of thing, a campaign brief ("Launch awareness for ...", "...
push ...", "Drive consideration ...", "Drive repeat purchase ..."); none is about pricing, competitors or brand voice. (3) Every
evaluation brand has an organisation of its own (D5), so "prior runs in this organisation" is "prior runs of this brand". (4) The
research the system does under the evaluation can only return evidence about a product's *category*, never the product (V7,
pseudonyms), so it cannot supply proof for a specific product's claims. (5) `runs.request` holds each run's request; nothing holds an
embedding of it.

---

## N: novelty of the request against prior runs

**What it needs.** A representation of the request, and the requests of earlier runs to compare it with. Data: the earlier requests
(exist in `runs.request`). Compute: a local embedding (the encoder is already loaded; no model call, no quota). Storage: none, or one
table.

| Option | How | Costs | Gives up |
|---|---|---|---|
| **N-a** (embed the request, compare with the same organisation's earlier approved requests) | Embed `category + sector + brief + audience` with the local encoder at step 5; `N = 1 - max cosine` over earlier runs in the organisation whose campaign was approved; `N = 1` with none. Computed on the fly from `runs.request`: no migration | One extra embedding per run (milliseconds, counted in `encoder_recall_ms`); re-embeds up to two earlier requests per brand in the evaluation | With one or two prior requests `N` is the cosine of short strings. Its values are unmeasured, and the objectives all begin "Launch awareness for ..." so spread may be small |
| **N-b** (novelty against what memory holds) | `N = 1 - max S` over the recalled items (S is the semantic similarity already computed at step 4). No new embedding, no store | None | It is not "prior runs": it measures how well memory matches, which `U` already measures, so the two terms count the same thing |
| **N-c** (drop `N`) | Remove the term; renormalise the other weights | None | The request's own novelty is invisible; depth then depends on memory coverage and sector alone |

**Measured 2026-11-01 (read-only; the local encoder over the 30 evaluation requests, text = category + sector + brief + audience; no
model call).** `N = 1 - max cosine` against the same brand's earlier requests: **campaign 2** (a different product of the brand)
n=10, min 0.138, mean 0.195, max 0.312; **campaign 3** (same product, new objective) min 0.117, mean 0.159, max 0.225; and, for
comparison, `1 - cosine` between two DIFFERENT brands' campaign 1 (n=45): min 0.224, mean 0.391, max 0.479. *Read:* the order is the
right one (a stranger is more novel than a different product, which is more novel than the same product), so `N-a` carries signal
and a cross-brand scope would not be needed. But the range is narrow: the encoder gives related marketing text cosines of
0.7 to 0.9, so within a brand `N` lives between about 0.12 and 0.31 and moves `R` by at most about 0.13. `N` does not separate the second run
from the third (0.195 against 0.159); `U` does that work. Raw `N` is not a calibrated novelty; the depth thresholds would have to
absorb that, and they are untuned.

**My pick: N-a, scoped to the organisation and to approved runs.** It is the only reading that matches the design's words, it
costs nothing in quota, needs no migration, and the measurement shows signal in the right order. Said plainly: it will not by
itself produce the cold-start progression.

## F: how fresh the answer has to be

**What it needs.** A request-type taxonomy that does not exist: a closed set of types, and a value of `F` for each.

| Option | How | Costs | Gives up |
|---|---|---|---|
| **F-a** (a closed request-type field) | Add `request_type` (for example launch, seasonal push, consideration, retention) and a config map type -> `F`. The owner classifies the 30 requests once, before any data, in a frozen lock like `categories.lock` | A field on the request, a config map of invented numbers, 30 hand classifications | The map is the developer's untuned judgement (like V5). And it carries almost no information here: four campaign-brief types, none of them pricing or competitor, would give `F` only two or three values |
| **F-b** (read the type from the brief's words) | A keyword list ("price", "offer", "competitor") | Small | A word list for a property of free text: the project refused that in the claim gate (T5, "no word list"). It would also fire on none of the 30 requests |
| **F-c** (staleness of what memory holds) | `F` from the age of the recalled items against their `freshness_days` | None | Not the design's meaning (a property of the *request*), and the M-score's own freshness term already removes stale items from coverage: the same fact counted twice |
| **F-d** (drop `F`) | Remove the term; renormalise | None | A freshness-sensitive request type (pricing, competitor) could not raise depth, in a system whose requests do not include one |

**My pick: F-d.** The evaluation's requests are all one kind, so `F` would be near-constant and could not move a depth; including it adds an
invented mapping that cannot be shown to matter. It can return when requests have types.

## E: claims needing external proof

**The design defines it only by that phrase.** No document says how to count "claims needing external proof", from which claims,
or what "needing" means.

| Option | How | Costs | Gives up |
|---|---|---|---|
| **E-a** (claims on file that are not verified) | `E` = the fraction of the product's active claims that are `deduced` (an inference with a confidence) rather than `verified`, read from `product_claims` at step 2. No model call | A query | In the evaluation the claims are entered `verified`, so `E` is about zero for nearly every request; and it counts claims *on file*, not claims *the brief needs* |
| **E-b** (unbacked property assertions in the brief) | The brief check's result (which property assertions in the brief no listed claim backs), already computed once per request and recorded | None (precomputed, 30 calls spent) | 1 of 30 briefs is flagged (R10), so `E` is 0 for 29 requests: no information |
| **E-c** (drop `E`) | Remove the term; renormalise | None | A request whose claims are mostly unproven cannot raise depth |

**Why `E` is the hardest, and the argument for dropping it.** Even a well-defined `E` asks for more research when claims lack proof.
Under the evaluation the research cannot deliver proof of a specific product's claims (fact 4), so a high `E` would spend depth on research
that cannot answer it. **My pick: E-c.** Said plainly: `E` cannot be defined without inventing a rule the design never specified.

## What R becomes, and what that costs

Dropping `F` and `E` leaves `N`, `U`, `S` at weights 0.30 / 0.25 / 0.15, renormalised to **0.43 / 0.36 / 0.21**. `R` stays in [0, 1] but a
non-regulated request now tops out at 0.79 and a regulated one adds 0.21. With the MEASURED `N` (means above, `S` 0): run 1 of a brand
(`N` 1, `U` 1) gives `R` 0.79, **D2**. A second run (`N` about 0.20) gives `R` about **0.44 (D1) if memory fills no slot (`U` 1)**, but
about **0.26 (D0) if it fills one of the two (`U` 0.5)**, and about 0.08 (D0) if both. A third run (`N` about 0.16, `U` 0) gives 0.07,
**D0**. So the progression D2, D1, D0 of `Memory.md` section 7 appears only if campaign 2's recall misses at `M >= 0.80` (a different
product, `P` 0.7, may well); if it hits once, run 2 goes straight to D0. The 0.35 threshold decides this more than any weight. The
thresholds remain untuned and are tuned on the tuning set, never the evaluation set. Fewer terms is a thinner "engine" for the dissertation; it is also a more defensible one: every input has a
source, a unit and a test.

## Two related holes, same pass

* **Dynamic agent selection** (`Architecture.md` section 7: "selected by request type, sector risk and memory coverage"). The pool is A1,
  A2, A3, A6, A7; A1, A6, A7 always run. A2 is already skipped at D0. **Proposed: A2 runs iff depth is not D0; A3 always runs**
  (personas feed `C` and write-back). That is 4 or 5 agents, not 3 to 7, recorded with its rationale per run.
* **Model routing** (`Architecture.md` section 4.2). `route()` names `complexity`, `is_structural`, a budget reserve floor and
  `prior_failures` escalation: none has a source. **Proposed: build only what has one** (T4 by function, the draft's high-risk
  signal, conflicting evidence, and the daily-quota downgrade and deferral); every agent stays Standard otherwise; leave the others out
  and say so. The Cheap tier stays unused until a call is shown to be structural.

## Descope: what is load-bearing for the hypothesis (asked 1 November 2026, with the 9 November freeze fixed)

*The hypothesis, as the evaluation tests it:* adaptive memory reduces work (ARR, SRR, CR, LR) while quality holds. A component is
**load-bearing** if removing it makes that comparison impossible or meaningless; **record as a limitation (a V-record)** if the
comparison survives without it.

| Component | Verdict | Reasoning | Build |
|---|---|---|---|
| Necessity engine (`R` -> D0/D1/D2, logged) | **Load-bearing** | It is the only thing that lowers search work: without it the Proposed arm always runs the depth requested and SRR, CR and LR are zero by construction. Needs the `N`/`U`/`S` decision above | about 1 day |
| Injection by band (verbatim / rephrase / context), with the injected items and their bands recorded per run | **Load-bearing** | Without it memory changes nothing the agents see (V14). The per-run record IS the "reuse depth" metric, and it is what shows memory reached the copy | about 2 days (new prompt versions for the agents that take it, their lock lines, tests that a recalled item changes an input, per band) |
| Item cap of 12, ranked by `M` | **Load-bearing, but small** | "Holding quality" needs memory unable to flood the prompt. With two kinds and 3 campaigns per brand the cap will rarely bind (a brand holds a handful of items), so it is a guard to build, not a measured effect | under half a day |
| Forced fresh research slot | **Needs a ruling: it contradicts D0** (below) | | |
| Dynamic agent selection | **Load-bearing only as one rule, already mostly built** | The pool is A1, A2, A3, A6, A7; A1/A6/A7 always run. A3 cannot be skipped (its persona feeds `C` and the write-back's persona rows). So the only agent that can vary is A2, and A2 already skips at D0. Varying it adds nothing beyond depth, and **ARR then equals the D0 share divided by five: collinear with SRR/depth.** Build the rule (A2 runs iff depth is not D0) and persist its rationale; do not build more | under half a day |
| Used-items record and reuse bump | **V-record** | "Which injected items the campaign USED" needs claim-level attribution nobody built; the injected items and bands are recorded (above), which is what the metrics need. The bump is +0.02 confidence at `M >= 0.90`: over three campaigns per brand it cannot change an outcome | none |
| Correction staging (`flagged`, `edited_out`) | **V-record** (your prior is right) | An edit-out names words in the copy, not a memory item, and no statement-to-item link exists, so a correction cannot be attributed. The poisoning defence is then stated as not operating, not faked | none |
| Daily-quota downgrade and deferral (C4/C9) | **V-record** (your prior is right, with a condition) | The evaluation needs about 660 Standard calls against 500 a day: the schedule (2.16, generation spread over days) avoids exhaustion, and a run that hits it is an infrastructure failure re-run by the attempt rule. **Condition:** Strong is 20 a day and the plan expects 18 to 24 strong calls in the regulated runs, so those runs must be scheduled over two days by hand; a T4 call must never be downgraded, and today it fails the run, which is safe | none (a scheduling note) |
| Rate-limit scheduler | **Already built enough** | The ledger paces to each model's RPM window and the gateway waits out a 429 without counting it as a retry; a per-model token bucket adds nothing the evaluation needs | none |
| Model routing policy (the Cheap tier, `complexity`, `prior_failures`) | **V-record** (not in your prior; I add it) | Every agent runs Standard, the Cheap tier is used by no call, and the strong tier is a Flash model close to Standard (`Architecture.md` section 4.1), so a routing effect cannot be measured here whatever is built. T4 routing by function exists | none |
| Admin view of the depth distribution | **V-record** | The distribution is a query over `runs.depth`; a chart is Phase 12 | none |

**The forced fresh slot contradicts D0.** `Memory.md` section 9 says "force at least one fresh research slot on every run regardless
of `R`"; `Architecture.md` section 6.1 defines D0 as "reuse memory only, zero searches". Both cannot hold: with the slot forced, no run is D0 and
SRR is never 100% for any run. *Options:* (a) **a D0 run performs ONE search** (the planner's first query), so D0 means "one fresh query"
and SRR for D0 is 3 of 4 or 7 of 8 saved, not all; (b) the slot is a memory slot never injected (the top item is held out of the prompt), which
needs no search but also does not test freshness; (c) drop it and state the echo-chamber risk as not defended. *My pick: (a)* with the
definition of D0 changed in the documents, because it is the only reading under which the slot is a fresh research slot and not a rename.
It lowers the headline SRR; that is the price of the guard, and it is stated, not hidden.

**The split, in one line.** Build: the engine (`N`, `U`, `S`), injection by band with its per-run record, the item cap, the A2-at-D0
selection rule with its rationale, and (a). Record as limitations: used-items and the bump, correction staging, the quota downgrade
and deferral, the rate-limit scheduler, model routing, the admin chart. About **4 to 5 build days, with tests and the freeze note**; this
is what fits before 9 November only if the rulings below are made today and nothing is added.

**Capacity, said plainly.** Until the 9th the same days also have to hold the blind presentation harness (needed on the 8th), the ten
campaigns for the agreement round (about 72 Standard calls: **that is beyond the 29 left of the standing 60, so it needs a ruling
on the budget**, though well inside the 500 a day), and the freeze of the rubric amendment. That is tight, with about one day of slack
if nothing slips, and a slip moves the freeze to the 10th or 11th, where the brand-cutting rule applies.

## The two proposals, one paragraph each, with my call (asked 1 November 2026)

**Dynamic agent selection: a limitation, apart from one rule that comes free.** The pool is A1, A2, A3, A6, A7. A1, A6 and A7 always
run, and A3 must run because its persona feeds the customer-match term `C` and the write-back's persona rows. That leaves one variable
agent, A2, and A2 is already skipped at D0 (it has no pages to read). So "adaptive agent selection" is the rule "A2 runs unless depth is
D0", a consequence of the engine, not a mechanism that could show its own effect: ARR would equal the D0 share divided by five and move
exactly with depth. Build the rule and persist its rationale; **record adaptive agent selection as a limitation (the design's 3 to 7 agents is 4 or 5 here)**.
It is not load-bearing for "adaptive memory reduces work".

**Model routing: a limitation.** Routing is a separate contribution from memory, and here it cannot be measured whatever is built: every
agent runs Standard, the Cheap tier is used by no call, the strong tier is a Flash model close to Standard (`Architecture.md` section 4.1), so a
tier difference is a narrow one, and the strong tier is reserved for T4 and high-risk calls by rule and already routed by function. Building
`complexity`, `is_structural` or `prior_failures` escalation would add code with no source for its inputs and no measurable effect. **Record it as a
limitation; keep T4-by-function routing as built.**

## Registered prediction for the freeze gate (written BEFORE the check is run)

*Rule (owner, 1 November 2026):* if `R` gives the same decision for campaigns 1, 2 and 3 of a brand, the adaptive mechanism is inert. The
prediction is stated first and the check reports what happened, whichever way.

* **Campaign 1 (cold start): D2** for every brand. `N` = 1 (no earlier request) and `U` = 1 (no memory), so `R` = 0.43 + 0.36 = 0.79 (plus 0.21 for a regulated
  brand, where `S` = 1); 0.79 >= 0.70.
* **Campaign 2 (a different product of the same brand): D1** for most brands. `N` about 0.20 (measured), and I predict `U` = 1: campaign 1's
  items describe the first product's category and a second product's recall should not reach `M >= 0.80` against them (`P` is 0.7 for the same brand,
  the text differs). Then `R` is about 0.44, in [0.35, 0.70).
* **Campaign 3 (the first product again, a new objective): D0** for most brands. `N` about 0.16, and I predict `U` <= 0.5 (the same product's items reach
  `M >= 0.80`). Then `R` is at most about 0.25, below 0.35.
* **What would falsify it:** campaigns 2 and 3 landing in the same band for more than a few brands, or all three the same. Then `R` is changed before anything is built on it.
  *Known weak point:* campaign 2 falls to D0 if memory fills even one of the two slots for it (`R` about 0.26), and the 0.35 threshold decides that more than any weight.

## RESULT of the freeze-gate check, stage 1 (1 November 2026): THE PREDICTION FAILED

`scripts/phase7_r_check.py`, real memory, no model call: brand B04's real items after its campaign 1 (4 insights, 1 competitor, from chunk F's first
campaign), recalled for its campaigns 2 and 3 with the real step-4 recall and the real encoder.

| Campaign | `N` | `U` (slots filled) | best `M` | `R` | depth | predicted |
|---|---|---|---|---|---|---|
| R10 (1) | 1.000 | 1.000 (0 of 2, cold start) | n/a | 0.786 | **D2** | D2 |
| R11 (2, other product) | 0.214 | 1.000 (0 of 2) | 0.715 | 0.449 | **D1** | D1 |
| R12 (3, same product) | 0.223 | 1.000 (0 of 2) | 0.771 | 0.453 | **D1** | **D0** |

**Campaigns 2 and 3 land in the same band, and `R` for them differs by 0.004.** Campaign 3 does not reach D0. Said plainly: with `R` as
registered, on the one brand that has real memory, the third campaign is decided exactly like the second, so the mechanism does not separate
them, and the experiment as designed would not show the memory effect. This is a stop: **`R` is changed before anything is built on it.**

*Why, from the numbers.* (1) `N` is 0.21 and 0.22 for campaigns 2 and 3: no discrimination, as the earlier measurement warned. (2) `U` carries the
rest and is 1.0 for both, because **no recalled item reaches `M >= 0.80`** (best 0.715 and 0.771), so coverage is 0 of 2 even for the SAME product. A new item starts at
confidence 0.60 (stated default, untuned) and its source score is 0.78, so its `E` is about 0.47, and a text match against a brief rarely scores `S`
above 0.7: an `M` of 0.80 is close to unreachable for a one-campaign memory. The reuse bands are PROVISIONAL and calibrated in chunk G, **after** Phase 7;
`R` cannot be tested before the bands are, and the bands are calibrated against the system `R` drives.

*Options (none chosen; each is a design change).* (a) **Count a slot as filled at the support band (`M >= 0.65`) instead of 0.80**: both campaigns then
fill both slots, `U` 0, `R` about 0.09, **both D0**: still no separation, and campaign 2 loses its D1. (b) **Place the coverage threshold between the two observed
values (0.72 to 0.77)**: it separates these two numbers, from one brand, and is a threshold fitted to the data it is then tested on: not defensible without more brands. (c) **Raise the
starting confidence or the source-score weight in `E`** so a good one-campaign item can reach 0.80: a tuning of `M`, again before the bands are calibrated. (d) **Define coverage
on the top item's `S` (semantic match) rather than `M`**, which does not carry confidence and freshness: closer to "does memory hold what this request needs", a change of definition.
(e) **Calibrate the bands first on the ten-brand structure** (chunk G's work, brought before Phase 7's `R`), using approver judgement: right in principle, and it moves the calendar. *My view:* (d) or (e);
(a) and (b) fix the symptom. **Stage 2** (the other nine brands, about 7 Standard calls each, about 63 in all, to see whether the failure is general) is NOT run: it needs a ruling on the budget
and on whether to change `R` first.

## What the owner is asked to rule

1. `N`: N-a (after the free measurement), N-b or N-c.
2. `F`: F-a, F-b, F-c or F-d.
3. `E`: E-a, E-b or E-c.
4. The agent-selection rule and the routing scope above.
5. The descope split above (build list and limitation list), and **the forced fresh slot: (a), (b) or (c)**.
6. The budget for the agreement round's ten campaigns (about 72 Standard calls, beyond the standing 60).

My picks are N-a, F-d, E-c, the descope split as written, and (a) for the fresh slot. None of it is built until ruled.

---

# Registration 2 (1 November 2026): U redefined, bands frozen, predictions, budget, variance check

**Registered before any stage-2 call.** The values below are in `config/scoring.yaml` (`necessity:`), pinned by `backend/tests/test_necessity_frozen.py`, and
committed before the first call. Owner rulings of 1 November 2026 (four, in the order below). Nothing here was fitted to a stage-2 outcome; one thing was seen
beforehand and is declared in "What was seen before registering".

## 1. U is redefined: quantity of relevant memory, not the best match crossing a threshold

*The reason, given in advance (owner's construct argument, independent of any number):* a binary threshold on the single best match discards the QUANTITY of
accumulated memory, and quantity is what the hypothesis is about. "Do I already know enough about this brand" is a question about how much usable material exists.

```
n        = the number of recalled items whose semantic match S reaches the relevance floor (S = cosine of request and item, clamped to [0, 1])
coverage = min(1, n / 12)                       12 = the injection cap (Memory.md section 9)
U        = 1 - coverage
R        = (0.30 N + 0.25 U + 0.15 S_sector) / 0.70       N = 1 - max cosine to the organisation's earlier requests; S_sector = 1 for a regulated sector
```

**The relevance floor is 0.55, set by a procedure and not from any campaign.** The null: S between one brand's 5 memory items and the 27 requests of the OTHER brands
(135 unrelated pairs; the local encoder, no model call). Its percentiles: 50th 0.399, 75th 0.430, 90th 0.458, 95th 0.488, 99th 0.531, maximum 0.553. **Floor = the 99th
percentile rounded UP to the next 0.05 = 0.55**: an unrelated item passes about once in a hundred. The procedure used no C2 or C3 similarity and does not move with any
outcome.

**The shape is the capped linear function, and that is deliberate.** "Saturating" is satisfied by saturating at the cap. Any smooth curve (exponential, logarithmic) adds a
free parameter (a time constant) that would be chosen by looking at what separates; the capped line adds none, because the cap is already the owner's. The cost is stated:
it saturates abruptly at 12 and gives no diminishing returns before it.

**Is the floor itself a new overfitting risk? Yes, it is the one new knob, and these are the guards.** (a) It comes from a null distribution defined before stage 2, with its
percentile and rounding rule fixed here. (b) Stage 2 reports, as SENSITIVITY ONLY and never to select, the same depths at floors 0.50 and 0.60. (c) If the verdict flips between
0.50 and 0.60, that is reported as fragility whatever the verdict at 0.55 is. (d) The floor is a property of the encoder, so a change of encoder re-derives it by the same procedure.

**Option (d), keeping semantic match in coverage:** it is kept, and it is already inside this definition: relevance IS the semantic match S, through the floor. M is deliberately not
used: it carries confidence and freshness (a one-campaign item starts at confidence 0.60), which is why stage 1 saw no item reach 0.80.

## 2. The bands are frozen now as provisional pre-registered values; chunk G becomes a RESULT

**Why the experiment can run on provisional bands.** The circle existed only if calibration were a prerequisite. It is not: frozen bands make chunk G a measurement, "how closely did the
pre-registered bands match approver judgement", reportable whichever way it comes out and stronger than a threshold fitted to the approver and called validated. I see no reason the
experiment cannot run on them. Three consequences, stated rather than discovered later: (i) **Phase 10's tuning of the depth thresholds is cancelled**; the dissertation reports the
as-frozen values. (ii) Freezing forbids SILENT revision, not revision: a change after chunk G is a new dated registration before the evaluation freeze, with both sets reported (Rules.md A5).
(iii) Stage 2 tests the DEPTH bands only. The reuse bands drive injection, which is not built, so they are frozen here and untested until Phase 7 builds it.

| Band | Frozen value | Rationale (written before any stage-2 run) |
|---|---|---|
| Reuse: verbatim | `M >= 0.90` | Memory.md section 3.2 default, set 2026-10-18 before any data; also the write-back gate (section 6.3). Unchanged. |
| Reuse: adapt | `0.80 <= M < 0.90` | Same source. **No longer feeds U** (section 1); it still decides whether an agent may rephrase an injected item. |
| Reuse: support | `0.65 <= M < 0.80` | Same source; background only, never the sole basis for a claim. Below 0.65 an item is not injected. |
| Depth: D2 | `R >= 0.32` | The coverage tercile: U >= 2/3 (at most 4 of 12 relevant items) at the reference novelty N = 0.20, S = 0: R = (0.30 x 0.20 + 0.25 x 2/3) / 0.70 = 0.324. "Memory holds under a third of a full prompt: research in full." |
| Depth: D0 | `R < 0.20` | The other tercile: U <= 1/3 (at least 8 of 12): R = (0.06 + 0.25 x 1/3) / 0.70 = 0.205. "Memory holds two thirds of a full prompt or more: reuse only." D1 between. |

The reference N = 0.20 is the 1 November free measurement of within-brand novelty (means 0.195 and 0.159). The design's 0.35 / 0.70 were set for a range whose typical maximum was 0.85; with F and E dropped
(decision-log 2.18) a non-regulated request tops out at 0.79, and 0.35 / 0.70 would put D1 where `U >= 0.72`, a band memory could reach only by holding almost nothing. The terciles are the
construct-based replacement; they are NOT the old numbers moved until the data looked right.

**Consequence for the one regulated brand (B07), stated now.** `S_sector` = 1 adds 0.214 to R, so a regulated request is never D0 whatever memory holds, and is D2 up to 10 relevant items and D1 only
at 11 or 12. B07 cannot show the progression by construction. That is the design's weight on sector risk acting as intended (regulated work always gets research), and it means the memory
benefit on a regulated brand is not measurable under these weights. It is a V-record, and B07 is excluded from the separation criterion below.

## 3. Registered predictions (campaigns 1, 2 and 3 per brand)

Depth is a deterministic function of `n` and `N` (tested): with N about 0.21, **n <= 4 gives D2, n = 5 to 8 gives D1, n >= 9 gives D0** (non-regulated). So what is predicted is `n`, which depends on how many
items a campaign writes. Observed once: B04's campaign 1 wrote 5 (4 insight, 1 competitor) at D1. Campaign 1 of the other brands runs at D2 (cold start), which may write more; I predict 4 to 8.

| Campaign | Brands B01-B03, B05, B06, B08-B10 (8 brands) | B04 | B07 (regulated) |
|---|---|---|---|
| **1** | n = 0, N = 1, R = **0.786, D2**: certain by construction | D2 | R = 1.00, **D2** |
| **2** (other product, same brand) | n predicted **4 to 7**: R 0.24 to 0.33, **D1 modal, about 60%**; alternative D2 (n <= 4, the 4-item edge); D0 only if campaign 1 wrote 9+ | contaminated (below) | R 0.43 to 0.51, **D2** |
| **3** (first product again, new objective) | n predicted **8 to 11** (campaign 1's plus campaign 2's items, less near-duplicates merged at 0.95): R 0.15 to 0.21, **D0 modal, about 55%**; alternative D1 (n = 8) | contaminated | R 0.31 to 0.37, **D2 at n <= 10, else D1** |

Within-brand differences I expect and register: B02 (smartwatch, then a calling smartwatch) and B06 (protein coffee, then a flavour of it) have a campaign-2 product close to campaign 1's, so more of campaign 1's items pass the floor: higher n(C2) than the
other brands, still D1 unless campaign 1 wrote 9 or more items.

**Pass criterion for "stage 2 separates", registered:** among the 8 clean non-regulated brands, **at least 6 show three different bands** (C1 > C2 > C3 in depth). **Falsified if** C2 and C3 share a band in more than 2 of the 8, or all three share a band in any
two. Reported beside it for every brand, pass or fail: n, N, U, R, depth for all three campaigns; n written per campaign; the sensitivity at floors 0.50 and 0.60; and R's monotonicity (C1 > C2 > C3 by at least 0.05) as the continuous version of the same question. **Known
weak point, said before the run:** the margins are thin where the bands are narrow (D1 is n = 5 to 8 only), and the thin edge is n(C2) = 4 against 5; if campaign 1 writes 4 items, C2 is D2 and the criterion fails for that brand.

**What was seen before registering.** B04's stage-1 result (best M 0.715 and 0.771, depths D2, D1, D1) was seen before this definition was chosen, and the count of 5 items per campaign is from B04. So **B04 is contaminated** and is re-scored for the record
only (it cannot test the prediction). The floor's null used B04's five items but no C2 or C3 similarity of B04's. The other nine brands are clean.

## 4. Development budget: ceiling 250, conditional, enforced in code

Owner: the ceiling is **250 calls, any tier, and the overrun (60 to 101) is the owner's**. It is conditional: **no further call of any tier until it is confirmed in writing that no billing account is linked to Cloud project `brandverse-ai`**; if one is linked, everything stops, stage 2
included, and the whole spend position is reassessed. Enforced in `LLMGateway`/`CallLedger.admit` the way the strong-tier cap is: `config/dev_budget.yaml` holds the ceiling (250), the owner's tally at the ruling (101), the databases allowed to call
(each with its own `llm_calls` count at the ruling) and `billing_unlinked_confirmed_on`. While that date is null **every call of every tier is refused** (a `refused` row, `DevBudgetRefused`); a database not listed is refused; the 251st is refused; a gateway that reaches the provider through a ledger without the
budget cannot be built. Tests: `tests/test_dev_budget.py` (decision, pinned values, the construction refusal) and `tests/db/test_llm_ledger.py` (refusals written, any tier and organisation). **Tally: 101 spent, 149 remain. Stage 2 needs about 126 (section 6), not the 63 I estimated for campaign 1 alone; about 23 would remain, and a repaired `invalid_output` costs an extra call, so the ceiling is tight.** The condition is
UNSET: I cannot read the billing fact and have not set it.

## 5. Variance check on the ten agreement campaigns (before the 8th)

They ran at D0 with no research, so nothing carries a citation. My forecast of the spread across the ten, per dimension, from reading the generated copy (a forecast by the developer, not a rating, and it must not reach the raters). Kappa is undefined for a dimension a rater scores
identically on all ten, and with a constant on one rater only it is zero or undefined: a property of the sample, not of the raters.

| Dimension | Expected spread | Constant? |
|---|---|---|
| Product relevance | Almost all copies state specifications from the product brief (a litre capacity, a warranty, a wattage). Expect 2 on 8 to 10 of 10, perhaps 1 on P04 (lip balm, two claims) and P07 | **Likely near-constant at 2: high risk** |
| Customer relevance | Copy is generic: "your daily routine", "your kitchen"; only P05 (commute, travel), P06 (wet days, school run) and P08 (journey) touch the stated audience. Expect mostly 1, a few 0 or 2 | Low spread; **medium to high risk of constant 1** |
| **Brand consistency** | **There are no stated voice attributes** for any of the ten (`products.yaml` states none; the copywriter is not given a voice; A1 defect 3, still awaiting the owner's answer). A rater can only give "neither matches nor contradicts" | **CONSTANT BY CONSTRUCTION: kappa undefined whatever the raters do** |
| Originality | Stock phrasing throughout ("Discover ...", "Streamline your routine", "peace of mind", "your daily routine"). Expect 0 or 1, little 2 | **High risk of near-constant** |
| Feasibility | The gate and the system disagree: 7 passed, 3 flagged, 2 needed a revision round. Expect a real mix of 1 and 2, some 0 (P03, P07, P09 flagged) | **Varies: the one dimension I expect to carry kappa** |
| Trustworthiness | No copy cites a source (D0). The anchor's "attributable to something stated in the copy or its cited source" is met only where the copy attributes itself (P10, "Based on manufacturer tests"). Expect 1 almost throughout, 0 for the three flagged | **High risk of near-constant at 1** |

**Decision needed from the owner:** the 8 November set as generated can show agreement on **feasibility only, with confidence**; it cannot show it on brand consistency whatever happens, and probably not on product relevance, originality and trustworthiness. Options: (a) mix a few
RESEARCHED campaigns in (needs calls, and cited claims make trustworthiness vary); (b) author voice attributes for the ten and regenerate (the generator was not given one, so the copy cannot be matched to it: 70 calls); (c) drop brand consistency from the 8 November analysis (A1 option b) and report which of the other five
are undefined, as limitations, with the sample's property named in advance. My recommendation: (c) for brand consistency now, and (a) for a handful (3 or 4 campaigns, run when billing is confirmed) only if the owner wants trustworthiness and product relevance to be measurable.

## 6. Stage 2 status

**Not run.** The billing condition is unmet (section 4), so the ledger refuses every call.

**A flaw in stage 1, found while writing the predictions, and what it changes.** Stage 1 scored campaign 3 against the memory campaign 1 wrote, not the memory campaigns 1 AND 2 wrote, because campaign 2 had never been run. Campaigns 2 and 3 therefore saw the same
memory and could not have separated except through which product they matched. The ruling's own description ("campaign 3 has roughly twice" what campaign 2 has) needs campaign 2's write-back to exist. So **stage 2 runs campaigns 1 AND 2 live for each of the nine brands (18 runs, about 7
Standard calls each, about 126 calls)**, replayed research as before, each approved through the product's own path; campaign 3 is then scored on the recalled memory with the real encoder and no model call (as stage 1 did), which is what makes it a recall measurement and not a third run.
Total spend then 227 of 250. If the owner prefers a margin: run the 8 clean brands only (112 calls, 213 total) and leave B07, which the criterion excludes anyway; or run four brands first (56 calls) and look. **The multi-brand runner is not written**
(`chunk_f_first_campaign` recreates its database, which would delete B04's memory, and runs R10 only); writing it needs no calls and is the next step once the order and the condition are settled.

## 7. Stage-2 runner (3 November 2026): scope, predictions per definition, the interim gate

**Written with no calls, committed before any call.** `backend/scripts/phase7_stage2.py`, tested by `backend/tests/test_phase7_stage2.py`. It refuses to run live unless this file, `config/`, the decision log and itself are committed and unmodified, so this registration provably precedes the first call.

**Scope (owner, 3 November): four brands first.** Order fixed here by position, not by expected outcome: the eight clean non-regulated brands are B01, B02, B03, B05, B06, B08, B09, B10; **the interim set is the first four: B01, B02, B03, B05.** B04 (contaminated) and B07 (regulated, excluded from the criterion) are not in the set. Campaigns 1 and 2 run live (about 7 Standard calls each, 56 for the four); campaign 3 is scored from recall with no call. Each brand has its own organisation, so no memory crosses brands. Campaign 1 runs at D2 (cold start, R = 0.786); **campaign 2 runs at D1**, the depth B04's campaign 1 was run at, because the engine that would choose it is not built: this is a free parameter of the run, fixed here, and it governs how many items campaign 2 writes.

**[SUPERSEDED by 7a: three variants, not two.] Both definitions of U, from the same recall of each campaign:** new (2.21: n at S >= 0.55, bands 0.20 / 0.32) and old (slots filled at M >= 0.80, bands 0.35 / 0.70, the stage-1 values kept as constants in the runner). Reported per campaign: N, n of recalled, slots, both U, both R, both depths, and the new depth at floors 0.50 and 0.60 (sensitivity, never to select).

**[SUPERSEDED by 7a: predictions for all three variants.] Predictions per definition (registered; modal first):**

| | Campaign 1 | Campaign 2 | Campaign 3 |
|---|---|---|---|
| New | D2 | D1 (alt D2) | D0 (alt D1) |
| Old | D2 | D1 | **D1: the old definition is predicted NOT to separate C2 from C3** |

Reason for the old row, given in advance: items one campaign writes start at confidence 0.60, so no recalled item reaches M >= 0.80, no slot fills, and old U stays 1 for campaigns 2 and 3 (stage 1's failure on B04). It is a prediction of a known failure mode, informed by B04, not a blind one.

**[SUPERSEDED by 7a: the preference ordering replaces "either" and its closing sentence.] Interim criterion (owner): at least 3 of the 4 brands show three distinct bands (campaign 1 > 2 > 3) under EITHER definition before any remaining brand runs.** Evaluated by `interim_verdict`; a partial interim (fewer than four brands scored) cannot pass. `--stage rest` reads the interim report and **refuses unless it exists and passed**: there is no flag that skips this. "Either definition" is two chances at the same threshold; both counts are reported, and a pass carried by the old definition alone would contradict 2.21's reason for replacing it, so it is reported as such.

**Found while writing it (no call):** (1) **research is recorded for R01 to R25 only.** B09's campaign 2 (R26) and all of B10 (R28 to R30) cannot be replayed, so at most six clean brands (B01, B02, B03, B05, B06, B08) can run campaigns 1 and 2 live; the criterion "at least 6 of 8 clean brands" is then reachable only if all six separate, or the recording is finished (R26 to R30, live searches, held until the owner says). (2) A no-call run of the live scoring path on B04's existing memory gave n = 1 of 5 recalled items past the floor for campaign 2 and 0 of 5 for campaign 3, against the registered 4 to 7 and 8 to 11 (R 0.419, 0.453: D2, D2). That memory holds only campaign 1's items, so it does not test the prediction, but it is the first sign that the 0.55 floor may be set where few real items pass. The floor is frozen and is not touched; if stage 2 shows the same, that is the finding.

## 7a. Owner's rulings of 3 November 2026 (eight), registered before any stage-2 call

**1. Three variants of U, one preference ordering.** All three are computed from the same recall, so the third costs no call.
- **OLD:** U = 1 - (slots whose best M reaches 0.80) / 2; stage-1 bands (D0 < 0.35, D2 >= 0.70).
- **FLOOR:** U = 1 - min(1, n/12), n = recalled items with S >= 0.55; frozen bands (0.20 / 0.32).
- **COUNT:** the same with **no floor**: n = every recalled item (capped at 12); the same frozen bands.

*Reason for COUNT, stated before the data (owner):* the floor gates n on similarity to the CURRENT request, so n can fall as memory grows, which the B04 figures show (n fell from 1 to 0 between campaigns 2 and 3 although the memory was the same). A quantity measure cannot behave that way. COUNT implements the construct argument of 2.21; FLOOR is that argument with a similarity gate still in it. *Cost of COUNT, stated now:* with no relevance gate, an unrelated item counts as much as a relevant one, so COUNT measures "how much memory the brand has", not "how much of it bears on this request"; within a brand of one sector that is a defensible reading, and the cross-brand null (2.21) is why the floor existed. Recall is already scoped to the organisation and brand, which is what makes COUNT usable.

**Registered preference ordering: OLD, then FLOOR, then COUNT; the first that meets the criterion is adopted**, whatever the later ones show. Reason: the original design is preferred because changing a mechanism is justified only when the original fails. This ordering, written before the run, is what makes three variants one test and not three chances; the interim report states the adopted variant and the count for each, and `--stage rest` refuses unless some variant met 3 of 4.

**Predictions (registered; modal first):**

| | Campaign 1 | Campaign 2 | Campaign 3 |
|---|---|---|---|
| OLD (**uncertain**) | D2 | D1 | D1 |
| FLOOR | D2 | D1 (alt D2) | D0 (alt D1) |
| COUNT | D2 | D1 (alt D2 if campaign 1 wrote 4 or fewer items) | D0 (alt D1 if the two campaigns wrote 8 or fewer between them) |

The OLD prediction is marked uncertain and **its basis is stage 1's artifact**: stage 1 scored campaign 3 against campaign 1's memory only, so "no recalled item reaches M >= 0.80" was seen on memory that campaign 2 had not added to. With both campaigns live, campaign 3 recalls more and older items, and whether any reaches 0.80 is not known. D1, D1 is the stage-1 expectation, held loosely.

**2. Campaign 2 runs at D1 (confirmed). Scaffold assumption, bias direction named:** D1 writes fewer items than D2 would, so it is the LESS favourable scaffold for separation: fewer items written by campaign 2 means a smaller n at campaign 3. A result that separates under D1 separates. The real experiment lets R choose the depth, so the scaffold is not the experiment; it is a lower bound on what memory accumulation does to R.

**3. Recording of R26 to R30 approved** (owner). It makes no model call, so neither the ledger nor the billing hold applies. It needs the machine on AC power (standing rule); it was on battery when this was written and was NOT started on battery. It keeps the full criterion at "at least 6 of 8 clean brands" (B09 and B10 become replayable) and a 10-brand freeze needs it regardless.

**4. The 8 November sitting needs no blind harness.** Ruling 2 of 1 November (a familiarisation/agreement sitting on ten campaigns from one system) leaves no arm to conceal, and both raters score identical items. The harness is needed only for the 16 November main round and is built after the freeze. It is therefore not on the path to the freeze, and the build days it was carrying are free.

**5. pytest-timeout approved** and added as a dev dependency (`pytest-timeout>=2.4.0`, resolved 2.4.0); `timeout = 300`, thread method, in `pyproject.toml`. The earlier `faulthandler_timeout` is replaced: it dumped but could not kill.

**6. The real-encoder path in tests was a hole, now closed. Cause:** importing LiteLLM (`app/llm/_litellm.py`) runs `dotenv.load_dotenv()` while `LITELLM_MODE` is `DEV` (the default), copying the repository's `.env`, `GOOGLE_API_KEY` included, into `os.environ`. `Settings(_env_file=None)` still reads the process environment, so every test that imported the gateway held a live key locally, `pipeline_agents` defaulted to `real`, and a test worker built the real pipeline and loaded the 1 GB encoder. CI has no `.env`, so it never did: local and CI differed, and the suite could not be shown unable to reach a provider. **Closed in three layers**, each shown to refuse in `tests/test_real_call_guard.py` in a subprocess handed a planted key: (a) `_litellm.py` sets `LITELLM_MODE=PRODUCTION` before the import; (b) an autouse fixture removes every provider key from the test environment; (c) the same fixture replaces the provider call (`app.llm._litellm.acompletion`, resolved when a gateway is built, no longer a default argument) and `LocalEncoder.load` without an injected constructor with a refusal. A test may opt out only by carrying `@pytest.mark.real_provider` or `real_encoder` AND being named in `tests/conftest.py`; both sets are empty, and a marker outside the set or a name without the marker fails collection.

**7. Freeze plan: 11 November with 7 brands. 9 November is upside only and is not the plan** in this or any other document. The seven are chosen from the recorded brands by the owner at the freeze; B01 to B08 are recorded, so the 7-brand branch does not depend on R26 to R30. Arithmetic with the harness removed (ruling 4): the build is 4 to 5 days (descope table), starting the day after stage 2 passes. If the ledger opens on 3 November and the interim passes that night, the build runs 4 to 8 November and the freeze is 8 November (4 days) or 9 November (5 days) with no slack. That is the upside. Every day the interim is delayed, or a rework of R is needed, moves it one-for-one; 11 November leaves two to three days of slack against 4 to 8 November, and is the plan. If the interim fails, there is no date until R is changed, and ruling 4 of 2.17 applies (cut brands, never campaigns).

**8. Pushed with 1 and 2 folded in.** This section is part of the registration that precedes the run.

## 7b. Registration 2, Amendment 1 (6 November 2026): the engine counts COUNT, not FLOOR

*Dated pre-registration amendment, by the project owner. Everything in sections 7 and 7a stands as a record; this changes which of the three registered definitions of U the BUILT engine uses.*

**The amendment.** `config/scoring.yaml` `necessity.u_source` is `count`: **U = 1 - min(1, n / 12), n = every recalled item** (no similarity floor), with the same frozen bands (D0 below 0.20, D2 from 0.32) and weights. The 0.55 floor stays in the file for the FLOOR column of the stage-2 report and for sensitivity; the engine no longer uses it. (Section 7a: 1 defines COUNT; ordering OLD, FLOOR, COUNT adopted the first that met the interim criterion, and none did, so the engine as first built used FLOOR, the registered Registration 2 definition.)

**Justification, in the owner's words and not in terms of outcomes.** FLOOR makes the necessity engine inert: it returns D2 for every campaign observed, in two independent runs (the stage-2 interim, 4 of 4 brands, decision-log 2.32 and 2.37; and the end-to-end confirmation run, 3 of 3 campaigns, 2.42). A mechanism that cannot vary cannot be evaluated. The switch is made to obtain a testable experiment, not a better result. It is NOT made because COUNT met the interim criterion (it did not: 2 of 4, then 1 of 4, against 3 needed).

**The construct argument against the floor is older than any stage-2 data.** Recorded 1 November (decision-log 2.21; section 2 of this document): a threshold on similarity to the current request "throws away the QUANTITY of accumulated memory, which is what the hypothesis is about", and 3 November, before any stage-2 call (decision-log 2.23; section 7a item 1, "Reason for COUNT, stated before the data"): the floor gates n on similarity to the CURRENT request, so n can fall as memory grows (the B04 figures, n 1 to 0 between campaigns 2 and 3 on the same memory), which "a quantity measure cannot do". COUNT implements that argument; FLOOR is that argument with a similarity gate left in it. *Honest limit of the citation:* the owner dates the argument to 3 to 4 November; I find it on 1 and 3 November and cite no 4 November entry on it.

**No evaluation data existed when this amendment was made.** Read-only against the live project on 2026-11-06: 0 runs, 0 `llm_calls`, 0 campaigns, 0 approvals, 0 memory items; 3 organisations, all `is_test`. The depths below are predictions registered before any evaluation run.

**Not changed, and why (owner, same ruling).** The reuse bands (0.90 / 0.80 / 0.65) are NOT touched. The same test (can the experiment be evaluated?) gives the opposite answer: nothing reached 0.80 and the highest M observed was 0.743, so only the support band fires, but memory still reaches A6 and can still affect the copy, so the quality question stays answerable. Re-basing thresholds onto observed values without the cannot-be-evaluated argument is fitting, and is refused. That only the support band fires is a finding about M's distribution and is reported beside every quality figure.

**PRE-REGISTERED PREDICTIONS under COUNT, across the full structure (10 brands x 3 campaigns), before any evaluation run.** *Model, fixed here:* campaign 1 is D2 (cold start). Campaign 1 writes n1 items, n1 taken from the five first-campaign yields observed at the repaired 10-page D2 budget: 9, 6, 12, 3 (attempt 24, decision-log 2.37) and 7 (the confirmation run, 2.42); a campaign at D1 writes about 5 items, at D2 about 7, and at D0 none (A2 does not run at D0: decision-log 2.38). Campaign k recalls all the brand wrote before it. N is computed from the real request texts with the local encoder; R, U and the depth come from the frozen functions through the engine's own `decide` (`backend/scripts/phase7_count_predictions.py`, which reproduces this table; no model call). Modal is at the mean yield, n1 = 7.

| brand | N (C2, C3) | campaign 1 | campaign 2 | campaign 3 |
|---|---|---|---|---|
| B01, B02, B03, B04, B05, B06, B08, B09 | 0.14 to 0.22 | D2 | **D1** (alt D2 if n1 <= 3; D0 if n1 >= 9) | **D0** |
| B10 | 0.30, 0.15 | D2 | **D1** (alt D2; D0 if n1 >= 12) | **D0** |
| B07 (regulated: S = 1) | 0.31, 0.13 | D2 | **D2** | **D1** (alt D2 if n1 <= 3) |

*By the model, by n1 (campaign 2 / campaign 3):* 3: D2 / D0; 6 or 7: D1 / D0; 9 or 12: D0 / D0 (non-regulated). A regulated brand never reaches D0: its sector term alone puts R at 0.21 or more.

**Predicted share of brands showing ANY depth reduction (below D2) by campaign 3: 9 of 10, 98 percent by the model** (every non-regulated brand under every observed yield; B07 in 4 of 5). **Predicted search reduction:** about 13.5 searches per brand-sequence against 24 for Baseline B, **SRR about 44 percent, WITH the mandatory fresh query counted, plausible range 35 to 55 percent.** Campaign 3 is predicted to be D0 almost everywhere, so most of the experiment's third campaigns are memory-plus-one-query runs by design.

**Reported openly.** FLOOR was the registered definition. It was built, tested end to end, and found inert; the switch, its reasoning, the gate result (not met), and the predictions above appear in the write-up. Nothing about this is hidden.
