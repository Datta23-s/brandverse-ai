# Runbook

Operational steps. A step here is a convenience; where a mistake would cost an evaluation run, the
CONTROL is a refusal in code, and the step says so.

## Starting the evaluation worker

```
EVALUATION_RUN=true
PIPELINE_AGENTS=real
RESEARCH_SNAPSHOT_DIR=C:\BrandverseData\evaluation-snapshot   # REQUIRED: the recorded snapshot
RESEARCH_STEP_TIMEOUT_S=900        # REQUIRED, 60 to 1140 s
WORKER_JOB_TIMEOUT_S=1800
MAX_LLM_CALLS_PER_RUN=20           # registered; not below 19 in production (start-up check)
```

- **The worker also refuses to start** without `RESEARCH_SNAPSHOT_DIR` naming a directory that holds
  `committed.jsonl` (`Settings`, decision-log 2.52). Without a snapshot the pipeline installs a
  research function that refuses live search, and every D1 and D2 run (Baseline B and the Proposed
  arm) fails; this used to be a line in this list and is now a refusal.
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

- **Generation is in three ROUNDS, one per campaign position, each with all three arms of every
  request adjacent in the registered order** (owner, 7 November 2026): `round:1`, `round:2`,
  `round:3`. This keeps the interleaving ruling: a provider outage lands across arms, never inside
  one. **In plain words: generation of round 2 cannot begin until every round 1 Proposed campaign
  has been approved or rejected by a human, and round 3 likewise.** Memory is written on approval
  (Rules.md T6), campaign N+1 reads it, and the approver is a person. The driver REFUSES the whole
  of `round:2` and `round:3` (Baselines included, because splitting a round would lose the
  adjacency) until `Runner.decided("proposed", N-1)` is true; the refusal is the control, this
  paragraph is not. **The approval sittings are the serialising step of the entire evaluation**:
  plan the days around them, not around compute. The slower schedule is accepted.
- **A run that does not settle stops the batch.** If a run is still not at an outcome after the
  Runner's wait, the attempt is recorded as `interrupted` and the batch STOPS for the owner; nothing
  is started beside it (a second run would double every count). Resuming checks that run first.
- **A re-run links to the run it replaced** (`runs.supersedes_run_id`, set by the backend at
  insert), and a run's pages fetched, facts and decision inputs are written when it parks, not
  only when it ends (migration 0048). Nothing the analysis needs lives only in the checkpoint.
- **The re-run cap (2) counts provider outages only.** A harness that is killed (power, a crash, a
  closed terminal) is recorded `interrupted`, listed in the report, its calls stay in the ledger,
  and it does NOT use the cap up: our own infrastructure dying is not a property of an arm. A third
  infrastructure failure of one campaign stops the batch and comes to the owner.
- Resume after any kill is the same command; the checkpoint file is the record. Never delete it.

## The R26 to R30 recording, and its deadline (owner, 7 November 2026)

R26 to R30 (B09 and B10) are not in the snapshot. Replay is keyed by query and a miss raises
`SnapshotMiss`, which is not an infrastructure failure, so those requests would end as the arms' own
failures. **They are recorded before generation or not run at all.** Attempt the recording on AC power
with the network healthy (`record_session.py`, resume command in the recording notes). **HARD
DEADLINE: the end of 9 November 2026.** If R26 to R30 are not committed by then, B09 and B10 are
EXCLUDED, the design is registered as **8 brands x 3 campaigns** before generation starts, and the
reason is recorded (the standing rule: cut brands, never campaigns per brand). This is decided here
so it is not discovered mid-generation. Generation does not start while the question is open.

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
