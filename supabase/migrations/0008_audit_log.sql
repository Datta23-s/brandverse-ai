-- 0008: append-only audit log.
-- Not reversible by design: dropping the audit trail destroys evidence. A rollback leaves it.
create table audit_log (
  id bigint generated always as identity primary key,
  occurred_at timestamptz not null default now(),
  org_id uuid,              -- null for platform actions; no FK: the log outlives the org
  actor_type text not null check (actor_type in ('user','service','agent')),
  actor_id text not null,   -- auth user id, service name, or agent id
  action text not null,
  entity_type text not null,
  entity_id text not null,
  details jsonb not null default '{}'::jsonb
);

create function app.audit_log_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'audit_log is append-only: % is not permitted', tg_op;
end $$;
create trigger audit_log_no_update_delete before update or delete on audit_log
  for each row execute function app.audit_log_append_only();
create trigger audit_log_no_truncate before truncate on audit_log
  for each statement execute function app.audit_log_append_only();

revoke update, delete, truncate on audit_log from public, anon, authenticated, service_role;
revoke insert on audit_log from public, anon, authenticated;              -- backend writes only

alter table audit_log enable row level security;
create policy audit_owner_read on audit_log for select to authenticated
  using (org_id is not null and app.has_role(org_id, array['owner']));
