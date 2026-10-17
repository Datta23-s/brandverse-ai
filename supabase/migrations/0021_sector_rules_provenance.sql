-- 0021: every sector states the provenance of its registry rules, and the regulated sector gets an
-- illustrative, synthetic rule set (decided by the project owner 2026-10-17).
--
-- The research claim is that the mechanism enforces whatever rules it is given — not that the
-- rules shipped are legally correct. Substituting a real, cited rule set is a deployment step,
-- outside the research contribution (Architecture.md §8.1). Until then health-supplements runs
-- against synthetic rules, so its status must be unmissable:
--
-- 1. sectors.rules_provenance: 'authoritative' (rules cited from regulation) or 'illustrative'.
--    The default is 'illustrative': a sector is never presented as authoritative unless the
--    backend says so; and today no sector has authoritative rules (the eight unregulated sectors
--    have none at all). Backend-settable only: API roles hold no write grant on sectors (0002).
--    Every report that names a sector prints this (app/core/sector_registry.py, describe()).
-- 2. A synthetic rule is marked by its source, and nothing citation-like: source_url is null and
--    source_note is exactly 'Synthetic rule, not derived from any regulation. Illustrative only.'
--    A rule with any other source must carry a source_url and a human author (created_by), as
--    before, and may not use that sentence. Synthetic rules need no human author: a migration
--    seeded them, and created_by does not pretend otherwise.
-- 3. A synthetic rule can only belong to an illustrative sector, and a sector cannot be marked
--    authoritative while it has an active synthetic rule (two triggers), so the label never lies.
-- 4. Four synthetic rules for health-supplements: two prohibited phrasings (a phrase and a regex)
--    and two required disclaimers (one on all output, one triggered), so disclaimer_text and the
--    declared "verbatim" rule are exercised by real rows, not only by tests. The disclaimer texts
--    say they are illustrative in the copy itself.
-- The project held no sector_constraints rows when this was written.
-- Down: delete is blocked by app.sector_constraints_versioned (rows are retired, never deleted);
--   as the table owner: alter table sector_constraints disable trigger sector_constraints_versioned;
--   delete from sector_constraints where source_url is null; enable it again; then
--   drop trigger sector_constraints_synthetic_sector on sector_constraints;
--   drop trigger sectors_provenance_consistent on sectors;
--   drop function app.sector_constraints_synthetic_sector(); drop function app.sectors_provenance_consistent();
--   alter table sector_constraints drop constraint sector_constraints_source,
--     alter column source_url set not null, alter column created_by set not null;
--   alter table sectors drop column rules_provenance;
alter table sectors
  add column rules_provenance text not null default 'illustrative'
    check (rules_provenance in ('authoritative', 'illustrative'));

alter table sector_constraints
  alter column source_url drop not null,
  alter column created_by drop not null,
  add constraint sector_constraints_source check (
    (source_url is null
       and source_note = 'Synthetic rule, not derived from any regulation. Illustrative only.')
    or (source_url is not null and created_by is not null
        and source_note is distinct from
            'Synthetic rule, not derived from any regulation. Illustrative only.')
  );

create function app.sector_constraints_synthetic_sector() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.source_url is null
     and not exists (select 1 from public.sectors s
                     where s.id = new.sector_id and s.rules_provenance = 'illustrative') then
    raise exception 'a synthetic rule can only belong to an illustrative sector';
  end if;
  return new;
end $$;
create trigger sector_constraints_synthetic_sector before insert on sector_constraints
  for each row execute function app.sector_constraints_synthetic_sector();

create function app.sectors_provenance_consistent() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.rules_provenance = 'authoritative'
     and exists (select 1 from public.sector_constraints c
                 where c.sector_id = new.id and c.source_url is null
                   and (c.retired_at is null or c.retired_at > current_date)) then
    raise exception 'sector % has active synthetic rules; retire them before marking it authoritative', new.id;
  end if;
  return new;
end $$;
create trigger sectors_provenance_consistent before insert or update of rules_provenance on sectors
  for each row execute function app.sectors_provenance_consistent();

insert into sector_constraints
  (sector_id, jurisdiction, kind, match_type, pattern, disclaimer_text, source_url, source_note,
   effective_from, created_by)
values
  ('health-supplements', 'IN', 'prohibited_phrase', 'phrase', 'miracle cure', null, null,
   'Synthetic rule, not derived from any regulation. Illustrative only.', '2026-10-17', null),
  ('health-supplements', 'IN', 'prohibited_phrase', 'regex', '\bguaranteed\s+results?\b', null,
   null, 'Synthetic rule, not derived from any regulation. Illustrative only.', '2026-10-17', null),
  ('health-supplements', 'IN', 'required_disclaimer', 'phrase', null,
   'Illustrative disclaimer (synthetic rule): consult a qualified professional before use.',
   null, 'Synthetic rule, not derived from any regulation. Illustrative only.', '2026-10-17', null),
  ('health-supplements', 'IN', 'required_disclaimer', 'phrase', 'protein',
   'Illustrative disclaimer (synthetic rule): protein content varies by serving size.',
   null, 'Synthetic rule, not derived from any regulation. Illustrative only.', '2026-10-17', null);
