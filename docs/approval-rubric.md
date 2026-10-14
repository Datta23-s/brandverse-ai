# BrandverseAI — Approver Rubric (decision D3)

| Field | Value |
|---|---|
| Document | `docs/approval-rubric.md` |
| Version | 1.2 — §2 presentation order (1.1) and §5–§6 calibration record (1.2) amended 2026-10-14, before calibration and run 1 |
| Date | 14 October 2026 |
| Governs | Step 12, human approval, for **every** run in **every** arm |
| Required by | `docs/evaluation-plan.md` D3 — must be committed before the first evaluation run |
| Depends on | `PRD.md` §5 step 12, §7.1 · `Rules.md` §5, §9 |

---

## 1. Why this document exists

One person approves all runs across all three arms. Without a written rubric that person drifts — stricter by run 40, more lenient by run 80, unconsciously kinder to the arm they hope wins. **Approver drift is a confound that lands directly on the dependent variable.** This rubric exists to make the approval decision mechanical enough to be repeatable and auditable.

The approver is not judging whether the campaign is *good*. The quality rubric (`PRD.md` §7.1, scored separately by two blind raters) does that. The approver is judging one narrower question:

> **Would it be defensible to publish this?**

A campaign can be mediocre and still approved. A campaign can be brilliant and still rejected.

---

## 2. Approver conduct

| Rule | Detail |
|---|---|
| **One person** | The same individual approves all runs in the evaluation. Name recorded in `evaluation-plan.md`. |
| **Blind to arm** | The harness strips arm labels before presenting a campaign. If the approver can infer the arm (for example from an obviously empty evidence pack), record that inference in the notes field. |
| **No editing** | The approver approves, requests revision, or rejects. They never fix the output themselves. Editing contaminates what is being measured. |
| **Presentation order** | **Constrained randomisation**, not plain randomisation. Generation and approval are decoupled: completed campaigns from every arm and brand enter one approval queue, which is shuffled into batches of **at most 15** (the session cap below). One constraint applies: a brand's campaign N and N+1 are **never adjacent** in the queue, in any arm and across batch boundaries (the last campaign of one batch and the first of the next count as adjacent); a shuffle that breaks it is redrawn. *Reason:* in the Proposed arm a brand's campaign N+1 is generated only after campaign N is decided (memory write-back needs approval), so an unconstrained queue in run order would show a brand's campaigns back to back and let the approver infer the arm from the sequence. The **shuffle seed and resulting order are logged per batch**, so the order is reproducible. Any residual arm inference is still recorded (`arm_inferred`, §5). Full procedure: `evaluation-plan.md`, "Points between the rubric and this plan". |
| **Time-boxed** | Maximum 10 minutes per campaign. Past that, record `undecided` and move on; undecided counts as a rejection in the results and is reported separately. |
| **Session cap** | Maximum 15 campaigns per sitting. Fatigue is a measurable source of drift. |
| **No consultation** | The approver does not discuss a campaign with the team before deciding. |

---

## 3. The gates

Evaluate in order. **Stop at the first failed blocking gate** and record that gate as the reason.

### Gate 1 — Truth (blocking)

| Check | Pass condition |
|---|---|
| T1.1 | Every factual product claim in the copy appears in the product's `verified` claim set, or is labelled as a deduction in the evidence pack |
| T1.2 | No claim contradicts a `verified` claim |
| T1.3 | No `restricted` claim appears in any form, including paraphrase |
| T1.4 | Every superlative ("best", "fastest", "leading") carries a cited source, or is absent |
| T1.5 | Numbers, specifications and units match their source exactly |

**Any failure → reject.** Record the failing sub-check.

> Note for option (a) evaluation: sources legitimately name the real manufacturer while the campaign uses a pseudonymised brand. That mismatch is expected and is **not** a T1 failure. See `evaluation-plan.md` RQ1.

### Gate 2 — Safety (blocking)

| Check | Pass condition |
|---|---|
| S2.1 | None of the 8 predatory anti-patterns present (`Architecture.md` §8) |
| S2.2 | No targeting of a vulnerable cohort by vulnerability |
| S2.3 | Regulated-sector copy carries its required disclaimer |
| S2.4 | No impersonation of a real brand or named person (`Rules.md` T8) |
| S2.5 | No content that would embarrass the institution if screenshotted |

**Any failure → reject.** S2.5 is deliberately subjective; record the reasoning in one sentence.

### Gate 3 — Completeness (revisable)

| Check | Pass condition |
|---|---|
| C3.1 | Strategy names a positioning, at least one channel, and at least one messaging pillar |
| C3.2 | At least one complete creative asset, not fragments |
| C3.3 | Evidence pack present and its links resolve |
| C3.4 | Declared gaps (from failed agents) are stated, not silently absent |

**Failure → request revision** if rounds remain, else reject.

### Gate 4 — Usability (revisable)

| Check | Pass condition |
|---|---|
| U4.1 | A marketer could act on this without asking a follow-up question |
| U4.2 | Copy is on-brand enough not to require a rewrite |
| U4.3 | The campaign addresses the stated objective, not a different one |

**Failure → request revision** if rounds remain, else reject.

---

## 4. The decision

| Outcome | When |
|---|---|
| **Approve** | All four gates pass |
| **Revise** | Gates 1 and 2 pass, Gate 3 or 4 fails, revision rounds remain |
| **Reject** | Gate 1 or 2 fails, or Gate 3/4 fails with no rounds left |
| **Undecided** | 10-minute box expired. Counts as a rejection; reported separately. |

**`budget_exhausted` is not automatically a rejection.** Judge the partial output on its own merits — a partial campaign that passes all four gates is approved, and the cap breach is recorded in the run log. This matters: treating every capped run as a rejection would make the cost cap look like a quality failure when it is a cost control working correctly.

---

## 5. What gets recorded

Per decision, one row in the `approvals` table (`supabase/migrations/0009_approvals.sql`; never in `campaigns.quality_scores`, which holds the blind raters' scores). Rows are append-only: a re-score is a new row.

```json
{
  "approver": "<initials>",
  "decided_at": "<timestamp>",
  "seconds_spent": 0,
  "outcome": "approve | revise | reject | undecided",
  "failed_gate": "T1.3 | S2.1 | ... | null",
  "reason": "<one sentence, required on anything but approve>",
  "arm_inferred": false,
  "arm_inference_note": null,
  "is_calibration": false
}
```

`seconds_spent` is not bureaucracy — if approval time differs systematically by arm, that is a finding about cognitive load, and you will want the data.

---

## 6. Calibration before the real runs

Do this before run 1. It takes under an hour and it is the difference between a defensible κ and an unexplainable one.

1. Generate **10 practice campaigns** on brands that appear in **neither** the evaluation set nor the tuning set, in a dedicated **practice organisation**, marked `organisations.is_practice = true`.
2. The approver scores all 10 against this rubric.
3. A second team member independently scores the same 10. The second scorer holds an active `approver` role **in the practice organisation only** — no role in any evaluation organisation — so the database's approver key and active-approver trigger apply to their decisions unchanged.
   Both scorers' decisions are recorded in `approvals` exactly as in §5, with `is_calibration = true`; the database rejects an approval in a practice organisation without it.
4. Compare. Any disagreement means the rubric is ambiguous at that point — **amend this document**, do not simply argue to agreement.
5. Re-run steps 2–4 until the two agree on at least 8 of 10.
6. Commit the amended rubric. Record the calibration date and the final agreement rate in `evaluation-plan.md`.

**Practice campaigns and their approvals are excluded from evaluation, never deleted.** `approvals` is append-only for the application (the limit is the table owner — `Architecture.md` §9), so calibration rows stay as the record of how agreement was reached. They never enter the results: the exclusion is enforced by a filter in the harness (`backend/app/evaluation/approvals.py`, filtering on `is_calibration`), which also excludes practice organisations, with tests that neither a calibration row nor an unflagged practice-org row ever reaches an evaluation query — not by discipline. This follows the precedent for seed memory (`Memory.md` §7.1: flagged, and filtered out by the harness). The filter is the flag, not the practice organisation's id: org ids are environment-specific and change on a reseed, whereas the flag travels with the row.

---

## 7. Mid-evaluation amendments

If the rubric must change after real runs have started:

1. Stop.
2. Record what changed, why, and at which run number.
3. Re-score every already-decided run against the amended rubric, or report the two periods separately.

**Never silently change the standard mid-evaluation.** An amended rubric with a documented re-score is a methodology note. An undocumented one is a discarded result.

---

## 8. Approver sign-off

> I have read this rubric. I will apply it as written, record a reason for every non-approval, and raise an amendment rather than deviate silently.

| Field | Value |
|---|---|
| Approver name | |
| Calibration date | |
| Calibration agreement | ___ / 10 |
| Rubric version applied | 1.2 |
