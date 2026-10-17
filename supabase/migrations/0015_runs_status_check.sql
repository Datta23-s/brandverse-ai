-- 0015: the run statuses a runs row may hold, as a CHECK (suggested in Phase 2, landed in Phase 3).
-- 'queued' (the default), 'completed', 'partial' (stopped by the budget, flagged budget_exhausted,
-- Rules.md C4) and 'failed' are written by backend/app/core/budgeted_run.py. 'blocked' is new: a
-- run a truth or safety gate stopped (Phases.md Phase 3) — a result in its own right, reported per
-- arm in Phase 10 (backend/app/evaluation/run_outcomes.py), never folded into 'failed'. Later
-- statuses (Phase 6's awaiting approval) are added by a new migration. The project held no runs
-- rows when this was written.
-- Down: alter table runs drop constraint runs_status_check;
alter table runs add constraint runs_status_check
  check (status in ('queued', 'completed', 'partial', 'failed', 'blocked'));
