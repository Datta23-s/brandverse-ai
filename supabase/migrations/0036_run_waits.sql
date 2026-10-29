-- 0036: when a run waited for a person, so latency can be measured with the wait removed
-- (Phase 6 chunk A, owner 2026-10-29: "you cannot subtract a wait you did not time").
-- A run parked at the human-approval interrupt waits for hours or days. PRD N1/N2 and the latency
-- the evaluation reports are the time the SYSTEM took, not the time an approver took, so the wait has
-- to be a recorded interval. Before this the park and the decision's arrival existed only as
-- timestamps inside the checkpointer's private tables (graph_checkpoints, in the library's format),
-- and the run row carried only created_at and completed_at.
-- One row per wait (a "revise" decision parks the run again, so a run can have several). Written only
-- by the backend: `suspend_run` opens it, the resume path closes it with the decision's arrival, and
-- `end_run` closes any still open so no ended run has an open wait. At most one is open per run.
-- Active latency = completed_at - created_at - sum(resumed_at - parked_at); runs.latency_ms is
-- derived that way by end_run, in the same statement as the status (never a counter, Rules.md A1).
-- Down: drop table run_waits;
create table run_waits (
  id bigserial primary key,
  org_id uuid not null references organisations(id) on delete cascade,
  run_id uuid not null,
  parked_at timestamptz not null default now(),
  resumed_at timestamptz,
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade,
  check (resumed_at is null or resumed_at >= parked_at)
);
create unique index run_waits_one_open_per_run on run_waits (run_id) where resumed_at is null;
create index run_waits_run_idx on run_waits (run_id);

alter table run_waits enable row level security;
create policy run_waits_read on run_waits for select to authenticated
  using (org_id in (select app.member_org_ids()));

-- Backend-only writes, never deleted (a raw log, like llm_calls: 0011, 0013).
revoke insert, update, delete, truncate on run_waits from public, anon, authenticated, service_role;
revoke usage, select on sequence run_waits_id_seq from public, anon, authenticated, service_role;
