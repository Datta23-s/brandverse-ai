-- 0038: the brief check is kept with the run (Phase 6, threat V11; owner, 2026-10-30).
--
-- Step 3 of the pipeline checks the request's brief for property assertions no listed claim backs
-- (app/core/brief_check.py): FLAG AND RECORD, never block. The result lived only in the run's
-- checkpointed state, and checkpoint state is not where evaluation data lives: how many briefs
-- carried unbacked properties, and which, is data about each request that the evaluation will
-- report and that decides whether flagging should ever become blocking.
--
--   runs.brief_check   the check as the run used it: {status, unbacked[], backed, prompt,
--                      brief_sha256, claims_sha256, source?} or null (a run with no brief, a run
--                      admission blocked, or one that predates this). Written by the backend with
--                      the run's terminal status, from the run's own state, like declared_gaps
--                      (0035): an API role cannot supply it at insert, and the insert trigger
--                      forces it null. For an evaluation run it is the request's PRECOMPUTED record
--                      (evaluation/brief_checks.json), so the three arms of a request carry the
--                      same value by construction.
--
-- Down: alter table runs drop column brief_check; and restore app.runs_initial_state() from 0035.

alter table runs
  add column brief_check jsonb
    check (brief_check is null or jsonb_typeof(brief_check) = 'object');

create or replace function app.runs_initial_state() returns trigger
language plpgsql set search_path = '' as $$
begin
  if current_user in ('authenticated', 'anon') then
    new.status := 'queued';
    new.depth := null;
    new.research_score := null;
    new.selected_agents := null;
    new.memory_hit_count := 0;
    new.search_count := 0;
    new.llm_call_count := 0;
    new.total_cost_usd := 0;
    new.latency_ms := null;
    new.budget_exhausted := false;
    new.declared_gaps := '[]'::jsonb;
    new.brief_check := null;
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
