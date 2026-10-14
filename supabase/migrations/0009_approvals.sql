-- 0009: approval decisions (docs/approval-rubric.md §5). One row per decision, including revise,
-- reject and undecided, which campaigns.approved_by cannot record. A revised campaign that comes
-- back gets a further row. Kept apart from campaigns.quality_scores, which holds the two blind
-- raters' scores (rubric §6), so an approver's decision and a rater's score are never mixed.
-- Down: drop table approvals; drop function app.approvals_active_approver();
--   alter table campaigns drop constraint campaigns_id_org_id_key;
alter table campaigns add constraint campaigns_id_org_id_key unique (id, org_id);  -- FK target

create table approvals (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  org_id uuid not null references organisations(id) on delete cascade,
  approver_id uuid not null,
  approver_role text generated always as ('approver') stored,
  outcome text not null check (outcome in ('approve','revise','reject','undecided')),
  failed_gate text,                         -- rubric gate id, e.g. 'T1.3'; null on approve
  reason text,                              -- required on anything but approve (rubric §5)
  seconds_spent int not null check (seconds_spent >= 0),
  arm_inferred boolean not null default false,
  arm_inference_note text,
  decided_at timestamptz not null default now(),
  foreign key (campaign_id, org_id) references campaigns (id, org_id) on delete cascade,
  -- approver_id must be a member of THIS org holding the approver role (Rules.md T6)
  foreign key (org_id, approver_id, approver_role)
    references org_members (org_id, user_id, role),
  check (outcome = 'approve' or nullif(btrim(reason), '') is not null),
  check (outcome <> 'approve' or failed_gate is null),
  check (not arm_inferred or nullif(btrim(arm_inference_note), '') is not null)
);
create index on approvals (campaign_id, decided_at);

-- The role FK cannot see revoked_at; a revoked approver is rejected here.
create function app.approvals_active_approver() returns trigger
language plpgsql set search_path = '' as $$
begin
  if not exists (select 1 from public.org_members m
                 where m.org_id = new.org_id and m.user_id = new.approver_id
                   and m.role = 'approver' and m.revoked_at is null) then
    raise exception 'approver_id must hold an active approver role in this organisation';
  end if;
  return new;
end $$;
create trigger approvals_active_approver before insert on approvals
  for each row execute function app.approvals_active_approver();

-- A decision is evidence (rubric §7: amendments are recorded, never silent). Rows are appended,
-- never edited: a re-score is a new row.
revoke update, delete, truncate on approvals from public, anon, authenticated, service_role;

alter table approvals enable row level security;

-- read any role. Decisions are written by the backend; the FK + trigger enforce that
-- approver_id is an active approver of this org regardless of who writes it.
create policy approvals_read on approvals for select to authenticated
  using (org_id in (select app.member_org_ids()));
