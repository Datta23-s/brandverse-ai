-- 0047: llm_calls.outcome 'provider_unavailable' (owner, 5 November 2026).
--
-- On 5 November the provider answered 503 "This model is currently experiencing high demand" to
-- most agent calls for over an hour. The gateway recorded each as 'error', which cannot be told
-- from a malformed request, and nothing retried it. The owner ruled that a provider-unavailable
-- answer (any 5xx) is an INFRASTRUCTURE failure, a fifth kind in the attempt rule of 2026-10-17
-- (docs/evaluation-plan.md, amendment of 2026-11-05), because an outage is not a property of the
-- arm being measured. The gateway now retries it twice with a backoff, each retry on a NEW row, and
-- aborts a run after three consecutive such answers (app/llm/gateway.py).
--
-- This outcome is how a call that the provider answered with a 5xx is recorded, so that:
-- * it counts toward SPEND (the daily quota, the run's call cap, the cost report): the provider
--   was asked;
-- * it is EXCLUDED from WORK (app/evaluation/llm_attempts.py), exactly as 'orphaned' is, so a
--   retry never inflates ARR: the row that finally succeeded is the work, the failed rows are spend;
-- * outage windows can be derived from the rows afterwards, so any run that touched one can be
--   identified (evaluation/outage_windows.yaml, app/evaluation/outages.py).
--
-- The constraint is replaced, not edited in place (Rules.md section 3.3): the new list is the old
-- one plus 'provider_unavailable'. Every existing row satisfies it.
-- Down: alter table llm_calls drop constraint llm_calls_outcome_check, add constraint
--   llm_calls_outcome_check check (outcome in ('pending','ok','invalid_output','error',
--   'rate_limited','refused','orphaned'));  (only once no row is 'provider_unavailable')
alter table llm_calls drop constraint llm_calls_outcome_check;
alter table llm_calls add constraint llm_calls_outcome_check
  check (outcome in ('pending', 'ok', 'invalid_output', 'error', 'rate_limited', 'refused',
                     'orphaned', 'provider_unavailable'));
