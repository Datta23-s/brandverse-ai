-- 0005: runs and the per-call LLM log the evaluation harness and org budget read.
-- Down: drop table llm_calls; drop table runs;
create table runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  product_id uuid,
  arm text not null default 'proposed'      -- 'baseline_a' | 'baseline_b' | 'proposed'
    check (arm in ('baseline_a','baseline_b','proposed')),
  status text not null default 'queued',
  request jsonb not null,
  depth text,
  research_score numeric(4,3),
  selected_agents text[],
  memory_hit_count int default 0,
  search_count int default 0,
  llm_call_count int default 0,
  total_cost_usd numeric(10,4) default 0,
  latency_ms int,
  budget_exhausted boolean default false,
  created_at timestamptz default now(),
  completed_at timestamptz,
  unique (id, org_id),
  foreign key (product_id, org_id) references products (id, org_id)     -- E7
);

-- Every call. The evaluation harness reads this table; org month-to-date spend sums it.
create table llm_calls (
  id bigserial primary key,
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  run_id uuid not null,
  agent_id text,
  model text not null,
  tier text not null,
  route_reason text not null,
  input_tokens int, output_tokens int,
  cost_usd numeric(10,6),
  latency_ms int,
  created_at timestamptz default now(),
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade
);
create index on llm_calls (org_id, created_at);

alter table runs      enable row level security;
alter table llm_calls enable row level security;

-- runs: read any role; start a run owner/editor. Status updates come from the backend.
create policy runs_read   on runs for select to authenticated
  using (app.has_role(org_id, array['owner','editor','approver','viewer']));
create policy runs_insert on runs for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));

create policy llm_calls_read on llm_calls for select to authenticated
  using (app.has_role(org_id, array['owner','editor','approver','viewer']));
