-- 0048: what a run knew that only the LangGraph checkpoint held, and which run replaced which
-- (owner, 7 November 2026, "before generation: these cannot be fixed afterwards").
--
-- DATA LOSS, by priority. Reading the evaluation path found values the analysis needs that exist
-- only in the checkpoint state of a run, not on any row:
--   pages_fetched   the LR fetch component (count difference x wall time per page) is computed from
--                   it; docs/evaluation-plan.md section 4 registers it as "measured directly, per
--                   arm per request". Until now only search_count was a column.
--   pages_served    pages the replay served of those fetched.
--   facts           jsonb: the rest of the research report (partial, uncovered facets, pages
--                   discarded for score), the memory read (coverage, slots, coverage after the
--                   personas, S of each recalled item), why each agent ran, the final revision
--                   round, and the findings (url, score, first-party flag) for the arms that write
--                   no memory and so keep them nowhere else.
-- All three are written by the backend when the run PARKS and again when it ends, so an approver
-- who takes days does not leave them in the checkpoint. NULL means "not recorded", never zero.
--
-- supersedes_run_id: the run this one re-ran. An infrastructure failure invalidates a run and the
-- harness runs the request again (docs/evaluation-plan.md Amendment 1); without a link nothing
-- says which run replaced which, and every read would count both. Set by the BACKEND when the new
-- run is inserted (the API derives it; no caller supplies it), forced null for an API role by the
-- insert trigger, and a run can be superseded at most once.
--
-- Down: drop index runs_superseded_once; alter table runs drop column supersedes_run_id,
--   drop column facts, drop column pages_served, drop column pages_fetched; and restore
--   app.runs_initial_state from 0046.
alter table runs
  add column pages_fetched int check (pages_fetched is null or pages_fetched >= 0),
  add column pages_served int check (pages_served is null or pages_served >= 0),
  add column facts jsonb check (facts is null or jsonb_typeof(facts) = 'object'),
  add column supersedes_run_id uuid,
  add constraint runs_not_self_superseding
    check (supersedes_run_id is null or supersedes_run_id <> id),
  add constraint runs_supersedes_same_org
    foreign key (supersedes_run_id, org_id) references runs (id, org_id);

create unique index runs_superseded_once on runs (supersedes_run_id)
  where supersedes_run_id is not null;

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
    new.pages_fetched := null;
    new.pages_served := null;
    new.facts := null;
    new.supersedes_run_id := null;
    new.created_at := pg_catalog.now();
    new.completed_at := null;
  end if;
  return new;
end $$;
