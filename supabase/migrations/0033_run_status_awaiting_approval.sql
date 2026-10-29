-- 0033: 'awaiting_approval' joins the run statuses a runs row may hold (Phase 6 chunk A, owner 2026-10-29).
-- A run the LangGraph pipeline has suspended at its human-approval interrupt (Architecture.md §5.3) is
-- neither queued, finished nor failed: it is waiting for a person, possibly for days, and the evaluation
-- must be able to tell it from a run that is still queued. It is written only by the backend
-- (backend/app/core/budgeted_run.py, `suspend_run`); API roles still cannot set any status (0005, 0016).
-- 0015 created the CHECK; this replaces it with the same list plus the new status, in the order the
-- application lists them (app/evaluation/run_outcomes.py RUN_STATUSES).
-- Down: update runs set status = 'queued' where status = 'awaiting_approval';
--   alter table runs drop constraint runs_status_check;
--   alter table runs add constraint runs_status_check
--     check (status in ('queued', 'completed', 'partial', 'failed', 'blocked'));
alter table runs drop constraint runs_status_check;
alter table runs add constraint runs_status_check
  check (status in ('queued', 'awaiting_approval', 'completed', 'partial', 'failed', 'blocked'));
