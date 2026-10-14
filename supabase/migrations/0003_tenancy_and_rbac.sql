-- 0003: organisations, membership roles (RBAC) and the role check used by every tenant policy.
-- Down: drop function app.has_role(uuid, text[]); drop table org_members; drop table organisations;
create table organisations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  monthly_budget_cap_usd numeric(10,2) not null default 50.00
    check (monthly_budget_cap_usd > 0),
  rate_limit_runs_per_hour int not null default 10 check (rate_limit_runs_per_hour > 0),
  rate_limit_requests_per_minute int not null default 60
    check (rate_limit_requests_per_minute > 0),
  created_at timestamptz default now()
);

-- One row per (org, user, role); a user may hold several roles. Revoked, never deleted.
create table org_members (
  org_id uuid not null references organisations(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  role text not null check (role in ('owner','editor','approver','viewer')),
  granted_by uuid references auth.users(id),
  granted_at timestamptz not null default now(),
  revoked_by uuid references auth.users(id),
  revoked_at timestamptz,
  primary key (org_id, user_id, role),
  check ((revoked_at is null) = (revoked_by is null))
);

-- The row's own org_id must be the caller's active org (JWT claim) AND the caller must hold one
-- of the roles there. No parent-table walking.
create function app.has_role(p_org uuid, p_roles text[]) returns boolean
language sql stable security definer set search_path = '' as $$
  select p_org = (auth.jwt() ->> 'org_id')::uuid
     and exists (select 1 from public.org_members m
                 where m.org_id = p_org and m.user_id = auth.uid()
                   and m.role = any (p_roles) and m.revoked_at is null);
$$;

alter table organisations enable row level security;
alter table org_members   enable row level security;

create policy org_read on organisations for select to authenticated
  using (app.has_role(id, array['owner','editor','approver','viewer']));
create policy org_owner_update on organisations for update to authenticated
  using (app.has_role(id, array['owner'])) with check (app.has_role(id, array['owner']));

create policy members_read on org_members for select to authenticated
  using (app.has_role(org_id, array['owner','editor','approver','viewer']));
create policy members_owner_insert on org_members for insert to authenticated
  with check (app.has_role(org_id, array['owner']));
create policy members_owner_update on org_members for update to authenticated
  using (app.has_role(org_id, array['owner'])) with check (app.has_role(org_id, array['owner']));
