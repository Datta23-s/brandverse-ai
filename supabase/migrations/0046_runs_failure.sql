-- 0046: runs.failure, why a run ended `failed` when the backend knows (owner, 2026-11-04).
--
-- Found in the stage-2 interim (2026-11-04): ARQ killed a run's job at its 600 s limit and the run
-- stayed `queued` with no error anywhere, which in a 90-campaign batch is indistinguishable from a
-- run that has not started. A job that runs out of time now ends its run `failed`, and this column
-- says why and where: {"kind": "job_timeout", "step": 6, "step_name": "research", "after_s": 1770,
-- "infrastructure_failure": "process_crash", "message": "..."}.
--
-- `infrastructure_failure` is one of the four kinds fixed in advance on 2026-10-17
-- (docs/evaluation-plan.md section 2, app/core/infrastructure.py). A job timeout is `process_crash`
-- there already; no fifth kind is added.
--
-- A new column, and not `declared_gaps` (0035): a gap is a failed agent ({step, agent, error,
-- message}) and `run_outcomes` counts them, so a timeout stored there would be read as an agent
-- failing. Written by the backend with the terminal status, exactly as `timings` (0044) and
-- `brief_check` (0038) are, so the insert trigger forces it null for an API role. Null for every
-- run that did not end in a recorded failure.
-- Down: alter table runs drop column failure; (and restore app.runs_initial_state from 0044)
alter table runs
  add column failure jsonb
    check (failure is null or jsonb_typeof(failure) = 'object');

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
    new.timings := null;
    new.failure := null;
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
