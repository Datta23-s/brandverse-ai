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
