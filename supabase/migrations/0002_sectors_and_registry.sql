-- 0002: sectors and the sector constraint registry (Architecture.md §8.1). Platform reference
-- data, not tenant data.
-- Down: drop table sector_constraints; drop table sectors;
--   drop function app.sector_constraints_versioned();
create table sectors (
  id text primary key,                              -- e.g. 'health-supplements'
  label text not null,
  regulated boolean not null default false,         -- Rules.md T4
  created_at timestamptz not null default now()
);

-- Human-authored only; versioned, never edited in place.
create table sector_constraints (
  id uuid primary key default gen_random_uuid(),
  sector_id text not null references sectors(id),
  jurisdiction text not null default 'IN',
  kind text not null check (kind in ('required_disclaimer','prohibited_phrase')),
  match_type text not null check (match_type in ('phrase','regex')),
  pattern text,               -- prohibited_phrase: wording that must not appear
                              -- required_disclaimer: trigger; null = required on all output
  disclaimer_text text,       -- required_disclaimer only: exact text that must appear
  source_url text not null,   -- the regulation or guidance this rule comes from
  source_note text,
  effective_from date not null,
  retired_at date,
  created_by uuid not null references auth.users(id),   -- a human; agent ids are not uuids
  created_at timestamptz not null default now(),
  check ((kind = 'prohibited_phrase'   and pattern is not null and disclaimer_text is null)
      or (kind = 'required_disclaimer' and disclaimer_text is not null)),
  check (retired_at is null or retired_at >= effective_from)
);

create function app.sector_constraints_versioned() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'sector_constraints rows are retired, never deleted';
  end if;
  if (to_jsonb(new) - 'retired_at') is distinct from (to_jsonb(old) - 'retired_at') then
    raise exception 'sector_constraints rows are versioned: only retired_at may change';
  end if;
  return new;
end $$;
create trigger sector_constraints_versioned before update or delete on sector_constraints
  for each row execute function app.sector_constraints_versioned();

revoke insert, update, delete, truncate on sectors, sector_constraints
  from public, anon, authenticated;                                       -- admin path only

alter table sectors            enable row level security;
alter table sector_constraints enable row level security;
create policy sectors_read on sectors for select to authenticated using (true);
create policy sector_constraints_read on sector_constraints for select to authenticated using (true);
