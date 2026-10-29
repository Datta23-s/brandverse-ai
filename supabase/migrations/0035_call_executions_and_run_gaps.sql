-- 0035: which execution made each call, and which gaps a run declared (Phase 6 chunk A, owner 2026-10-29).
--
-- 1. llm_calls: step, revision_round, execution_id.
-- A graph node that is running when its worker dies is run again from the checkpoint (at-least-once:
-- the checkpointer's contract). If it makes a model call, the log then holds two rows for one logical
-- call (the interrupted attempt's 'pending' reservation, or its finished row, and the retry's), and
-- nothing on a row said so: a re-execution, a revision round (step 10 legitimately runs up to three
-- times in a run) and the gateway's own repair call are indistinguishable AFTER THE FACT. So every
-- call made by a graph step records the step (1 to 15), the revision round it ran in, and an id for
-- that one execution of the step's body. A re-execution is then a second execution_id under the same
-- (run, step, agent, revision_round); an attempt number is DERIVED from the log (dense_rank over
-- first-call time), never kept as a counter (Rules.md A1). The three are all null (a call made outside
-- the graph: a smoke call, a script) or all set. See docs/evaluation-plan.md, "Re-executed steps".
--
-- 2. runs.declared_gaps: the agents that failed in a run that nonetheless ended 'completed'
-- (Architecture.md section 5: one failing agent never aborts a campaign). Before this, a completed
-- run with a failed agent and a complete run were the same row. `partial` (a budget stop) was already
-- told apart by its status and budget_exhausted; a declared gap was not told apart by anything.
-- Written by the backend with the terminal status, never by a caller: 0016's insert trigger is
-- replaced here to force it to its initial value for API roles, as it does every outcome column.
--
-- Down: alter table llm_calls drop constraint llm_calls_execution_identity,
--         drop column step, drop column revision_round, drop column execution_id;
--       alter table runs drop column declared_gaps;
--       then recreate app.runs_initial_state() as in 0016.
alter table llm_calls
  add column step smallint check (step between 1 and 15),
  add column revision_round smallint check (revision_round >= 0),
  add column execution_id uuid,
  add constraint llm_calls_execution_identity check (
    (step is null) = (execution_id is null) and (step is null) = (revision_round is null)
  );

create index llm_calls_execution_idx on llm_calls (run_id, step, revision_round, agent_id)
  where step is not null;

alter table runs
  add column declared_gaps jsonb not null default '[]'::jsonb
    check (jsonb_typeof(declared_gaps) = 'array');

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
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
