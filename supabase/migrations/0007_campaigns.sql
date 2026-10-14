-- 0007: campaigns and the approval constraint (Rules.md T6).
-- Down: drop table campaigns; drop function app.campaigns_active_approver();
create table campaigns (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  run_id uuid not null,
  strategy jsonb, creative jsonb, validation jsonb,
  status text default 'draft'
    check (status in ('draft','awaiting_approval','approved','rejected')),
  approved_by uuid,
  approver_role text generated always as ('approver') stored,
  approved_at timestamptz,
  quality_scores jsonb,
  foreign key (run_id, org_id) references runs (id, org_id) on delete cascade,
  -- approved_by must be a member of THIS org holding the approver role (Rules.md T6)
  foreign key (org_id, approved_by, approver_role)
    references org_members (org_id, user_id, role),
  check (status <> 'approved' or (approved_by is not null and approved_at is not null))
);

-- The role FK cannot see revoked_at; a revoked approver is rejected here.
create function app.campaigns_active_approver() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.approved_by is not null
     and (tg_op = 'INSERT'
          or new.approved_by is distinct from old.approved_by
          or new.status is distinct from old.status)
     and not exists (select 1 from public.org_members m
                     where m.org_id = new.org_id and m.user_id = new.approved_by
                       and m.role = 'approver' and m.revoked_at is null) then
    raise exception 'approved_by must hold an active approver role in this organisation';
  end if;
  return new;
end $$;
create trigger campaigns_active_approver before insert or update on campaigns
  for each row execute function app.campaigns_active_approver();

alter table campaigns enable row level security;

-- read any role. Approval is written by the backend; the FK + trigger enforce that
-- approved_by is an active approver of this org regardless of who writes it.
create policy campaigns_read on campaigns for select to authenticated
  using (app.has_role(org_id, array['owner','editor','approver','viewer']));
