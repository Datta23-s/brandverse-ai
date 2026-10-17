-- 0013: llm_calls records rate-limit waits (Rules.md C10) and each call's outcome.
--
-- rate_limit_wait_ms: time spent waiting on rate limits — pacing to the model's RPM and waiting
--   out 429s — kept apart from latency_ms, so the evaluation can report latency with and without
--   free-tier waits (Architecture.md §4.3, docs/evaluation-plan.md §4). A wait is not a failure
--   and never counts toward Rules.md C8's retry cap.
-- outcome: budget and quota accounting derive from this table, never from a separate counter
--   (Rules.md A1). A call's row is written BEFORE the call, as 'pending', in the same locked
--   transaction that checks the caps, so concurrent calls cannot both pass a check one of them
--   should fail; the row is completed after the call. 'refused' records a pre-flight refusal
--   with its reason (Rules.md C9: "log the reason in llm_calls.route_reason") and is never counted
--   as a call. Every other outcome reached, or may have reached, the provider and is counted.
-- finished_at: when the row was completed (NULL while pending).
--
-- A 'pending' row older than llm_pending_expiry_s (900 s; app/core/config.py, which checks it
-- exceeds the longest a live call can stay pending) is an abandoned reservation and is not
-- counted. It stays in the log as it is, and is reported as abandoned.
--
-- The raw log is written only by the backend's own role. As 0010/0011 did for DELETE and
-- TRUNCATE, INSERT and UPDATE are revoked from the API roles: before this, service_role (which
-- bypasses RLS) could insert or rewrite rows the budget and the evaluation read (Rules.md A1).
-- authenticated keeps SELECT through llm_calls_read (0005). Stated per column in
-- backend/tests/db/test_table_posture.py.
--
-- llm_calls holds no rows on the project when this applies (checked before the push), so the
-- column defaults rewrite nothing and no index is built; none is added here.
-- Down: grant insert, update on llm_calls to anon, authenticated, service_role;
--   alter table llm_calls drop column finished_at, drop column outcome,
--   drop column rate_limit_wait_ms;
alter table llm_calls
  add column rate_limit_wait_ms int not null default 0 check (rate_limit_wait_ms >= 0),
  add column outcome text not null default 'ok'
    check (outcome in ('pending', 'ok', 'invalid_output', 'error', 'rate_limited', 'refused')),
  add column finished_at timestamptz;

revoke insert, update on llm_calls from public, anon, authenticated, service_role;
