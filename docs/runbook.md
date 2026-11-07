# Runbook

Operational steps. A step here is a convenience; where a mistake would cost an evaluation run, the
CONTROL is a refusal in code, and the step says so.

## Starting the evaluation worker

```
EVALUATION_RUN=true
PIPELINE_AGENTS=real
RESEARCH_STEP_TIMEOUT_S=900        # REQUIRED, 60 to 1140 s
WORKER_JOB_TIMEOUT_S=1800
MAX_LLM_CALLS_PER_RUN=20           # registered; not below 19 in production (start-up check)
```

- **The worker refuses to start** unless `RESEARCH_STEP_TIMEOUT_S` is set inside the range
  (`Settings.research_timeout_range_s`: 60 s up to the job timeout minus the sequential model calls
  that follow research, 1800 - 11 x 60 = 1140 s). The refusal is the control; this list is not
  (decision-log 2.39, owner 6 November 2026).
- The same refusal applies to `ENVIRONMENT=production` with real agents.
- Evaluation calls count against `config/evaluation_budget.yaml` (960 = 660 + 300, report at 800)
  for the databases listed there, never against the development ceiling.
- On AC power only for any timed job (a sleeping laptop failed the runs of 5 November).
- The strong tier is 20 requests a day: the regulated runs that need 18 to 24 are scheduled over
  two days by hand.

## Generating the evaluation campaigns (`app/evaluation/generation.py`)

- **The Proposed waves are serialised by the approval sittings. In plain words: generation of
  Proposed round 2 cannot begin until every round 1 campaign has been approved or rejected by a
  human, and round 3 likewise.** Memory is written on approval (Rules.md T6), campaign N+1 reads it,
  and the approver is a person. The driver REFUSES `proposed:2` and `proposed:3` until
  `Runner.decided("proposed", N-1)` is true; the refusal is the control, this paragraph is not. The
  two Baselines never touch memory and generate in one wave. **The approval sittings are the
  serialising step of the entire evaluation**: plan the days around them, not around compute.
- **The re-run cap (2) counts provider outages only.** A harness that is killed (power, a crash, a
  closed terminal) is recorded `interrupted`, listed in the report, its calls stay in the ledger,
  and it does NOT use the cap up: our own infrastructure dying is not a property of an arm. A third
  infrastructure failure of one campaign stops the batch and comes to the owner.
- Resume after any kill is the same command; the checkpoint file is the record. Never delete it.

## The 16 November rating sitting (rubric amendment A2, section 2; non-negotiable)

1. **Joint calibration first (about fifteen minutes).** Both raters score **three campaigns
   together**, discuss where they differ, then score the **45 independently, in silence**. The three
   calibration campaigns are **excluded from the kappa**; the kappa is computed on the 45.
2. **Own hand.** Each rater writes their own scores on their own paper sheet, in their own hand.
3. **Photographed.** Every sheet is photographed before anyone leaves the room.
4. **Literal transcription.** The analysis is transcribed from the photographs exactly as written.
5. **No AI tool touches rater output at any stage, for any purpose, including tidying, reading
   handwriting or checking arithmetic.** The 6 November round was verbal and processed by an AI tool,
   so it has no primary record; this sitting is the kappa of record and has to withstand a reader
   asking where the numbers came from.
6. If originality floors across all three arms, report it as a finding about the system's output,
   not as a rating result (A2, section 4).
