-- 0009: approval decisions (docs/approval-rubric.md §5). One row per decision, including revise,
-- reject and undecided, which campaigns.approved_by cannot record. A revised campaign that comes
-- back gets a further row. Kept apart from campaigns.quality_scores, which holds the two blind
-- raters' scores (rubric §6), so an approver's decision and a rater's score are never mixed.
-- Calibration decisions (rubric §6) are stored here too, flagged is_calibration, and excluded from
-- evaluation by the harness (app/evaluation/approvals.py) — the same pattern as seed memory
-- (Memory.md §7.1, memory_items.is_seed). The flag, not the practice org's id, is the filter:
-- org ids are environment-specific and change on a reseed.
-- Down: drop table approvals; drop function app.approvals_active_approver();
--   drop function app.approvals_practice_is_calibration();
--   alter table campaigns drop constraint campaigns_id_org_id_key;
--   alter table organisations drop column is_practice;
--   grant update on organisations to authenticated;
alter table campaigns add constraint campaigns_id_org_id_key unique (id, org_id);  -- FK target

-- The calibration practice organisation (rubric §6), marked by a flag rather than identified by
-- id, so the rule below survives a reseed. Set by the backend only: owners keep update on the
-- other organisation columns but not on this one.
alter table organisations add column is_practice boolean not null default false;
revoke update on organisations from authenticated;
grant update (name, monthly_budget_cap_usd, rate_limit_runs_per_hour,
              rate_limit_requests_per_minute) on organisations to authenticated;

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
  is_calibration boolean not null default false,          -- rubric §6 practice row
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

-- Every decision in a practice organisation is a calibration decision, so an unflagged practice
-- row cannot exist to leak into evaluation. The harness also excludes practice orgs.
create function app.approvals_practice_is_calibration() returns trigger
language plpgsql set search_path = '' as $$
begin
  if not new.is_calibration
     and exists (select 1 from public.organisations o
                 where o.id = new.org_id and o.is_practice) then
    raise exception 'approvals in a practice organisation must have is_calibration = true';
  end if;
  return new;
end $$;
create trigger approvals_practice_is_calibration before insert or update on approvals
  for each row execute function app.approvals_practice_is_calibration();

-- A decision is evidence (rubric §7: amendments are recorded, never silent). Rows are appended,
-- never edited: a re-score is a new row.
revoke update, delete, truncate on approvals from public, anon, authenticated, service_role;

alter table approvals enable row level security;

-- read any role. Decisions are written by the backend; the FK + trigger enforce that
-- approver_id is an active approver of this org regardless of who writes it.
create policy approvals_read on approvals for select to authenticated
  using (org_id in (select app.member_org_ids()));
