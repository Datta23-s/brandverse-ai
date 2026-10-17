-- 0016: a run inserted by a signed-in user starts in the server's initial state, whatever the
-- caller sent. runs_insert (0005) lets an owner or editor insert a runs row through the REST API,
-- and before this the caller chose every column — a row could arrive already 'completed' or
-- 'blocked', with invented counts and cost, and be counted as a result (Rules.md A1).
-- Architecture.md §9, "Trust-deciding columns": any column that decides a trust level or a reported
-- result is derived by the server, never supplied by the caller. The API roles keep what is theirs
-- to choose (org_id, product_id, arm, request: the composite foreign keys and runs_insert check
-- them); the outcome columns are forced to their initial values. Updates need no trigger: no API
-- role has an update policy on runs (0005), so only the backend's own role moves a run on
-- (backend/app/core/budgeted_run.py). The backend's role is not touched by this trigger.
-- Down: drop trigger runs_initial_state on runs; drop function app.runs_initial_state();
create function app.runs_initial_state() returns trigger
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
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
create trigger runs_initial_state before insert on runs
  for each row execute function app.runs_initial_state();
