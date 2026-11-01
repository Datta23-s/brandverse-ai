# Phase 7, first decision: where R's inputs come from

| Field | Value |
|---|---|
| Status | **Options for the owner. Nothing here is ruled and nothing is built.** |
| Date | 2026-11-01 |
| Needed by | Phase 7 build start; the freeze is fixed for 9 November |
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

## What the owner is asked to rule

1. `N`: N-a (after the free measurement), N-b or N-c.
2. `F`: F-a, F-b, F-c or F-d.
3. `E`: E-a, E-b or E-c.
4. The agent-selection rule and the routing scope above.
5. The descope split above (build list and limitation list), and **the forced fresh slot: (a), (b) or (c)**.
6. The budget for the agreement round's ten campaigns (about 72 Standard calls, beyond the standing 60).

My picks are N-a, F-d, E-c, the descope split as written, and (a) for the fresh slot. None of it is built until ruled.
