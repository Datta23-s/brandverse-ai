# Rubric amendment A2: the human instrument, rebuilt from A1's anchor text

| Field | Value |
|---|---|
| Document | `docs/rating/rubric-amendment-2.md` |
| Amends | `docs/rating/rubric-amendment-1.md` section 3 (the anchors), `docs/approval-rubric.md` as used by raters |
| Dated | 6 November 2026 |
| Status | **Written, not frozen.** Freezing is the owner's act (section 6) |

## 1. Provenance, recorded first and plainly

The 6 November round used two human raters who rated **verbally**. **No written rater sheets exist.** Their responses were captured
and processed by an AI tool, so **no figure from that session is a primary record.** The session is **formative only**: a training and
rubric check, as registered on 1 November. **No score, agreement percentage or kappa from it is reported as a result anywhere in the
write-up.** The changes below are derived from defects in A1's own anchor text, **not** from that session's numbers, and each is
justified by the wording alone.

A1 itself was never frozen (`docs/rating/rubric-amendment-1.lock` does not exist). A2 therefore replaces A1's section 3 as the
instrument of record for 16 November; A1's section 4 defects are resolved or overtaken as listed in section 3 below.

## 2. Protocol for 16 November (runbook, non-negotiable)

1. Each rater writes their own scores on their own sheet, in their own hand.
2. The sheets are photographed before anyone leaves.
3. Transcription into the analysis is literal.
4. **No AI tool touches rater output at any stage, for any purpose, including tidying.**

That session is the kappa of record and it has to withstand a reader asking where the numbers came from.

## 3. The fixes, each justified by the anchor text

1. **Feasibility.** "Publishable" is ambiguous between factually safe and commercially usable; the gates already decide the first.
   Anchors: 0 a marketing team would rewrite this before publishing it; 1 a marketing team would publish it after small edits; 2 a
   marketing team would publish it as written. *Instruction on the sheet:* "Judge the writing, not the facts. Factual accuracy is
   checked separately and must not affect this score."
2. **Product relevance.** A1's anchors restated Gate 1 ("names a feature the product does not have", "every specific claim matches the
   product brief"), so the dimension double-counted truth. Anchors now measure specificity: 0 could describe any other product in the
   same category; 1 identifies the product type but nothing specific to this product; 2 contains at least one detail specific to this
   product. *Instruction:* "Do not penalise factual errors here. The gates cover them."
3. **Customer relevance.** A1's 0/1 boundary overlapped for generic copy. Merged: 0 addresses a different audience than the stated
   one; 1 addresses no particular audience, or fits any audience equally; 2 names a need, situation or vocabulary specific to the
   stated audience.
4. **Trustworthiness is removed from the human instrument.** As worded it asks humans to re-perform the truth check the gates already
   make objectively. Truth is reported from the gates (T1.x).
5. **Raters no longer tick gates.** T1.x, S2.x, C3.x and U4.x are machine-checked, and raters cannot see the strategy, evidence pack
   or failed-agent declarations in the packet. Raters give the dimension scores and the approve / revise / reject outcome, nothing else.
6. **Brand consistency is DROPPED.** A2's own condition was: brand voice must appear in the 16 November packet or the dimension is
   dropped. **None of the ten evaluation brands carries a voice attribute:** `evaluation/test_set.yaml` states no voice or tone for any
   of B01 to B10, `evaluation/tuning_brands.yaml` states none, and `brands.voice_guidelines` is empty (A1 section 4, item 3, still
   true). The only stated voices in the repository belong to three other fictional brands (Mettlepan, Zarvik, Nookwell) in the
   memory-relevance corpus, which are not in the evaluation. A rater cannot score a match to something nobody wrote down, and no arm
   was given a voice. It is therefore not in the instrument, and no report may claim a brand-consistency result. Reinstating it needs
   voice attributes for all ten brands, given to every arm, and its own amendment.

## 4. The instrument

**Four dimensions, one 0 / 1 / 2 scale, plus the outcome.** Agreement is linear-weighted Cohen's kappa per dimension beside the raw
counts (exact, within one point) and a confidence interval, as in A1 section 2.

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| **Product relevance** | could describe any other product in the same category | identifies the product type but nothing specific to this product | contains at least one detail specific to this product |
| **Customer relevance** | addresses a different audience than the stated one | addresses no particular audience, or fits any audience equally | names a need, situation or vocabulary specific to the stated audience |
| **Originality** | (A1 anchor, unchanged) entirely stock phrasing; could appear in any advertisement for any product | (A1 anchor, unchanged) competent but familiar; at most one fresh element | (A1 anchor, unchanged) contains at least one line the rater has not seen before, and does not open with a stock phrase |
| **Feasibility** | a marketing team would rewrite this before publishing it | a marketing team would publish it after small edits | a marketing team would publish it as written |

Originality is carried over word for word because the instruction did not change it. A1 section 4, item 4 records its known defects
(0/1 overlap; "not seen before" measures the rater). They are **not** fixed here and are not hidden: expect it to have the lowest
kappa, and report it with that caveat.

**Outcome.** The rater records approve, revise or reject for the copy they see, answering the approval rubric's own question
(`docs/approval-rubric.md` section 1): "Would it be defensible to publish this?" Approve: yes as it stands. Revise: yes after changes.
Reject: no. This is the rater's judgement of the copy. It is **not** the system's gate-derived outcome, which is computed by machine
and reported separately.

Evidence quality, cost efficiency and every T1.x to U4.x check are computed and **never shown to raters.**

## 5. What the gates, not the raters, now carry

Truth (T1.x), safety (S2.x), completeness (C3.x) and usability (U4.x) are reported from the machine checks. A1's ruling that raters
tick every failed check (A1 section 6) is withdrawn for raters because raters no longer see what those checks need.

## 6. Freeze

Freezing is a file hash and a timestamp (`scripts/freeze_rating_amendment.py`, which today freezes A1's path only; extending it to A2
is a small change made when the owner says to freeze). **Not frozen yet.** Nothing is scored from these sheets until it is, and a
change after the freeze is A3, never an edit.
