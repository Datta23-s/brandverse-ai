# Rubric amendment A1: the human-rated quality dimensions

| Field | Value |
|---|---|
| Document | `docs/rating/rubric-amendment-1.md` |
| Amends | `PRD.md` section 7.1 (the quality rubric), `docs/approval-rubric.md` sections 3 and 5 (failed gates) |
| Dated | 1 November 2026 |
| Run number at amendment | 0 (no evaluation run has happened; `docs/approval-rubric.md` section 7) |
| Status | **DRAFT, awaiting the owner's answers to section 4. Not frozen.** See section 5 for how it is frozen |

## 1. Provenance, as a limitation (V24)

**These definitions and scales were authored by the project's advisor on 1 November 2026. They are not derived from a published
instrument and have not been validated.** Their anchors are the advisor's judgement of what a rater can observe. Whatever the
inter-rater agreement turns out to be, it measures whether two raters can apply *these* anchors alike, not whether the
anchors measure originality, relevance or trustworthiness as the literature defines them. A reader is told this wherever a
quality figure from this rubric is reported (`docs/decision-log.md`, V24).

## 2. The scale

Six human-rated dimensions, **one 3-point ordinal scale for all of them: 0, 1, 2.** Three points rather than five because untrained
raters on a five-point scale disagree through scale noise rather than judgement. **Agreement is reported as linear-weighted Cohen's
kappa per dimension** (the points are ordinal, so 0 against 2 is a bigger disagreement than 0 against 1), **beside the raw counts**
(exact agreement, agreement within one point) and a confidence interval.

*Evidence quality* (a computed fraction of claims with resolvable sources) and *cost efficiency* (tokens per approved campaign) are
computed and are **never shown to raters**.

## 3. The anchors AS AUTHORED (owner, 1 November 2026)

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| **Product relevance** | names a feature, use or property the product does not have, or could describe a different product entirely | accurate, but nothing in it identifies this product specifically | every specific claim matches the product brief, and at least one detail is specific to this product |
| **Customer relevance** | addresses a different audience than the stated one, or no identifiable audience | fits the stated audience, but would fit most other audiences equally well | names a need, situation or vocabulary specific to the stated audience |
| **Brand consistency** | contradicts at least one stated voice attribute | neither matches nor contradicts the stated voice | matches at least two stated voice attributes |
| **Originality** | entirely stock phrasing; could appear in any advertisement for any product | competent but familiar; at most one fresh element | contains at least one line the rater has not seen before, and does not open with a stock phrase |
| **Feasibility** | needs substantive rewriting before the brand could publish it | publishable after minor edits | publishable as written |
| **Trustworthiness** | contains a claim a reader would doubt without evidence | believable, but nothing in the copy supports it | every notable claim is attributable to something stated in the copy or its cited source |

## 4. Defects found in the draft anchors (asked for before the raters use them)

Each is an overlap between two anchors or an anchor a rater cannot apply from what they are shown. A proposed replacement is given
for the owner to accept, change or reject; none is in force.

1. **Product relevance, 0 against 1 overlap.** The second clause of 0 ("could describe a different product entirely") describes the
   same copy as 1 ("nothing in it identifies this product specifically"). A rater cannot tell which applies. *Proposed:* 0 is only
   "names a feature, use or property the product does not have".
2. **Customer relevance, 0 against 1 overlap.** "No identifiable audience" (0) is copy that "would fit most other audiences equally
   well" (1). *Proposed:* 0 is only "addresses a different audience than the stated one"; "no identifiable audience" moves to 1.
3. **Brand consistency is UNOBSERVABLE as stated.** Every anchor refers to "stated voice attributes", and there are none:
   `evaluation/test_set.yaml` states no voice or tone for any brand, `brands.voice_guidelines` is empty, and the copywriter is not
   given a voice (the brand-voice vector and gate are specified, not implemented: decision-log 3.5). A rater cannot score a match to
   something nobody wrote down, and the generator could not have followed it. *Options for the owner:* (a) author two to four voice
   attributes for each of the ten evaluation brands, show them to raters, AND give them to the copywriter (a new prompt version before
   the freeze); (b) drop the dimension from the human rubric until voice exists (the PRD already says it is not implemented); (c)
   score it with no stated voice and report it as near chance. *Advisor's lean:* (b).
4. **Originality, 0 against 1 overlap and an anchor that measures the rater.** "Entirely stock" (0) is "at most one fresh element" with
   zero fresh elements (1). And "a line the rater has not seen before" (2) depends on the rater's reading history and drifts within a
   sitting: the system repeats its phrasing across campaigns ("Discover the shoes today"), so a line is "unseen" for the first
   campaign and "seen" for the fifth. *Proposed:* 1 is "exactly one fresh element"; 2 is "at least one line that could not be
   predicted from the product brief and the objective alone"; "stock phrase" is defined by an example list on the sheet. Expect
   this dimension to have the lowest kappa.
5. **Feasibility, "substantive" against "minor" is undefined, and it duplicates the approval outcome.** *Proposed:* minor = changes to at most
   one sentence or phrase and no claim removed; substantive = more than one sentence changes or any claim must be removed. Note that
   Feasibility 2 is close to "approve" and 0 to "reject" under the approval rubric (Gate 4, U4.2): the two will correlate and cannot be
   read as independent.
6. **Trustworthiness depends on a reader's doubt, "notable" is undefined, and a copy with no claims has no score.** The PRD's method
   ("hallucination audit, manual": check each claim against the verified claim set) is replaced by an impression, and it overlaps Gate 1
   (T1.1, T1.2). *Proposed:* define a claim as any statement of fact about the product; 0 = at least one claim not in the product brief;
   1 = every claim is in the brief but one is stated more strongly than the brief states it; 2 = every claim is in the brief and no
   stronger; a copy with no claims scores 2 and is noted.
7. **Ten campaigns cannot estimate a kappa.** With 10 subjects, one disagreement moves a weighted kappa by 0.1 or more, and kappa is
   undefined when a rater gives every campaign the same score (likely: most copy will be 1). The 8 November round (section 6) can find
   ambiguous anchors, which is its purpose; it cannot estimate agreement, and its kappa is not reported as a result. The main round's
   sample (30 or 90 campaigns) can.

## 5. Freeze

The amendment is **frozen before the 8 November sitting and before either rater sees any evaluation copy.** Freezing is a file hash
and a timestamp, not a promise: `scripts/freeze_rating_amendment.py` writes `docs/rating/rubric-amendment-1.lock` (the SHA-256 of
this file with line endings normalised, the UTC time, the freezer) and refuses to run twice; `backend/tests/test_rating_amendment_lock.py`
fails if the file differs from its lock afterwards. A change after the freeze is a new amendment (A2) with its reason and the number
of the last scored campaign, never an edit (`docs/approval-rubric.md` section 7). **Not frozen yet: waiting for the owner's answers to section 4.**

## 6. Rulings recorded with this amendment (owner, 1 November 2026)

* **Every failed check is ticked, not the first.** `docs/approval-rubric.md` section 3 said "stop at the first failed blocking gate and
  record that gate". Two raters who agree completely about the copy can then disagree about the *reason* through gate ordering alone
  (practice piece 5 fails T1.2 and S2.1). Raters tick EVERY check that failed; the "first failed gate" is derived afterwards from the
  documented order (Gate 1, 2, 3, 4), and the outcome follows from that order as before. The scoring sheet has one tick box per check.
  *Not yet changed:* `approvals.failed_gate` holds one gate, so the approver's own decisions in the system still record one; a
  `failed_gates` array needs a migration and is not made here.
* **The 8 November sitting is RATER AGREEMENT on the quality rubric, using 10 campaigns from the current system.** It measures whether
  two humans apply the rubric alike and does not depend on Phase 7. **The agreement round uses PRE-Phase-7 output: valid for rater
  agreement, INVALID for band calibration.** Band calibration against approver judgement stays in chunk G, after Phase 7 is frozen, on the
  post-Phase-7 system.
* **If the experiment must shrink, cut brands, never campaigns per brand.** Campaign 3 is where the memory progression shows. Freeze by
  about 9 November: 10 brands x 3 campaigns. By about 11 November: 7 brands x 3. Later than that: move the main rating round, do not cut further.
