-- 0044: runs.timings, the parts of a run's time that only the run knows (owner, 2026-11-02).
--
-- `runs.latency_ms` is one wall figure (wall time minus the waits for a person, 0036). One figure
-- cannot be compared across arms: on this hardware, under replayed research, it is dominated by
-- LOCAL compute (the encoder), and the Proposed arm embeds more than either baseline. The run is
-- therefore recorded in components:
--   model     summed from llm_calls (derived, not stored here)
--   waits     from run_waits (derived, not stored here)
--   search_ms the time spent in the search provider's calls (about zero under replay; recorded
--             anyway so a live run is comparable)
--   research_other_ms  the rest of the research step (replayed fetches, extraction, scoring)
--   encoder_recall_ms / encoder_writeback_ms  local embedding time, measured separately
-- Everything not accounted for is a RESIDUAL computed by app/evaluation/latency.py, never folded
-- into a named component.
--
-- Written by the backend with the terminal status from the run's own state, exactly as
-- `declared_gaps` (0035) and `brief_check` (0038) are, so the insert trigger forces it null for an
-- API role.
-- Down: alter table runs drop column timings; (and restore app.runs_initial_state from 0038)
alter table runs
  add column timings jsonb
    check (timings is null or jsonb_typeof(timings) = 'object');

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
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
