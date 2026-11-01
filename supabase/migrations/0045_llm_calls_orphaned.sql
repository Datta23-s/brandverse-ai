-- 0045: llm_calls.outcome 'orphaned' (owner, 1 November 2026).
--
-- A call is written 'pending' when it is admitted and completed when the provider answers. A worker
-- killed between the two leaves the row 'pending' for ever: a state that means "in flight" for a
-- call that is not. The recovery sweep (app/workers/recovery.py) now moves such rows to a terminal
-- 'orphaned' state when a worker starts.
--
-- SPEND versus WORK (app/evaluation/llm_attempts.py): an orphaned call may have reached the
-- provider, so it counts toward SPEND (the daily quota, the run's call cap, the cost report) and
-- is EXCLUDED from WORK (the per-campaign figure ARR is computed from). A re-executed attempt counts
-- once in work and every time in spend. A crash during a long batch therefore cannot inflate ARR.
--
-- The constraint is replaced, not edited in place (Rules.md section 3.3): the new list is the old one
-- plus 'orphaned'. Every existing row satisfies it.
-- Down: alter table llm_calls drop constraint llm_calls_outcome_check, add constraint
--   llm_calls_outcome_check check (outcome in ('pending','ok','invalid_output','error',
--   'rate_limited','refused'));  (only once no row is 'orphaned')
alter table llm_calls drop constraint llm_calls_outcome_check;
alter table llm_calls add constraint llm_calls_outcome_check
  check (outcome in ('pending', 'ok', 'invalid_output', 'error', 'rate_limited', 'refused',
                     'orphaned'));
