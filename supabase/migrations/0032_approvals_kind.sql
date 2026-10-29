-- 0032: an approval has a KIND: evaluation, calibration or tuning (owner, 2026-10-28).
-- Calibration is the 10-campaign exercise measured against the >= 8 of 10 bar (docs/approval-rubric.md
-- section 6). The tuning organisation (docs/evaluation-plan.md, "Measuring s") is a practice
-- organisation too, and its approvals must not land in the calibration record: a reader could not
-- tell which approvals were the calibration and which were incidental to measuring the strong calls a
-- run takes. So the kind is a column, the calibration figure counts kind = 'calibration' only, and a
-- tuning approval cannot be both.
-- is_calibration stays (the harness's evaluation read and its tests filter on it) and is tied to the
-- kind by a check, so the two cannot disagree.
-- Down: drop trigger approvals_practice_kind on approvals; drop function app.approvals_practice_kind();
--   alter table approvals drop constraint approvals_kind_matches_flag;
--   alter table approvals drop column kind;
--   then recreate app.approvals_practice_is_calibration() and its trigger as in 0009.
alter table approvals add column kind text not null default 'evaluation'
  check (kind in ('evaluation', 'calibration', 'tuning'));

-- Existing calibration rows are calibration rows (a no-op on a database that holds none).
update approvals set kind = 'calibration' where is_calibration;

alter table approvals add constraint approvals_kind_matches_flag
  check (is_calibration = (kind = 'calibration'));

-- Replaces 0009's rule that every decision in a practice organisation is a calibration decision:
-- it is now calibration OR tuning there, and tuning exists only there. An evaluation approval
-- still cannot sit in a practice organisation, so an unmarked practice row cannot exist to leak
-- into evaluation. The harness also excludes practice organisations.
drop trigger approvals_practice_is_calibration on approvals;
drop function app.approvals_practice_is_calibration();

create function app.approvals_practice_kind() returns trigger
language plpgsql set search_path = '' as $$
begin
  if exists (select 1 from public.organisations o
             where o.id = new.org_id and o.is_practice) then
    if new.kind not in ('calibration', 'tuning') then
      raise exception 'approvals in a practice organisation must have kind calibration or tuning';
    end if;
  elsif new.kind = 'tuning' then
    raise exception 'tuning approvals belong to a practice organisation';
  end if;
  return new;
end $$;
create trigger approvals_practice_kind before insert or update on approvals
  for each row execute function app.approvals_practice_kind();
