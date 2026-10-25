-- 0028: the evidence behind a memory item - sources, their scores, and the item's derived E input
-- (Memory.md section 3.1, decided 2026-10-25 by the project owner).
--
-- An item backed by several web pages carries the MAXIMUM of their source scores as the source
-- score that E multiplies by confidence. The rule can change later without refetching anything
-- because the per-source scores are stored, not only the aggregate:
--
--   sources              one row per page per organisation (its URL, host and own stated date).
--                        Stored once and shared by every item resting on it, so "this source turned
--                        out to be bad" is an indexed query - every item resting on it - not a scan
--                        of a JSON column.
--   memory_item_sources  one row per (item, source): the score, the factors it was made of, and
--                        what computed it and when. The score depends on the query that found the
--                        page (specificity), so it belongs to the pair, not to the source alone.
--   memory_items.source_score  DERIVED: the maximum of the item's rows, null if it has none.
--
-- The score is trust-deciding (the trust-deciding-columns rule, Architecture.md section 9):
--   - written by the backend only. No API role holds INSERT or UPDATE on either table, and a signed-in
--     user cannot set memory_items.source_score (the column is outside the grant of 0006, and a
--     trigger refuses a write that does not come from the derivation);
--   - recorded by the server: computed_at is set by the database whatever the writer sent, and
--     scorer and config_version name what computed the score, as a revocation names its cause;
--   - append-only: rows are never updated or deleted, and TRUNCATE is revoked and trapped. Changing
--     the scoring rule is a migration that says so, as raising revoke_below is (Memory.md 6.3).
-- Both tables are tenant-scoped (org_id, composite foreign keys, row-level security: members read).
--
-- Adds a unique (id, org_id) to memory_items so the link can reference an item together with its
-- organisation. Non-concurrent: the project held no memory_items rows when this was written.
--
-- Down: drop table memory_item_sources; drop table sources;
--   drop trigger memory_items_source_score_derived on memory_items;
--   drop function app.memory_items_source_score_derived();
--   drop function app.memory_item_sources_record();
--   drop function app.evidence_append_only();
--   alter table memory_items drop column source_score, drop constraint memory_items_id_org_id_key;

alter table memory_items
  add column source_score numeric(5,4) check (source_score between 0 and 1),
  add constraint memory_items_id_org_id_key unique (id, org_id);

create table sources (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  url text not null check (url like 'http://%' or url like 'https://%'),
  host text not null check (host <> '' and host = lower(host)),
  published_on date,                          -- the page's own stated date; null when it states none
  created_at timestamptz not null default now(),
  unique (org_id, url),
  unique (id, org_id)
);
create index on sources (org_id, host);

create table memory_item_sources (
  memory_item_id uuid not null,
  source_id uuid not null,
  org_id uuid not null,
  score numeric(5,4) not null check (score between 0 and 1),
  components jsonb not null check (jsonb_typeof(components) = 'object'),   -- the factors, what was left out
  scorer text not null check (scorer <> ''),                -- what computed it, e.g. source_scoring/1
  config_version text not null check (config_version <> ''),   -- scoring.yaml's version at the time
  computed_at timestamptz not null default now(),
  primary key (memory_item_id, source_id),
  foreign key (memory_item_id, org_id) references memory_items (id, org_id),
  foreign key (source_id, org_id) references sources (id, org_id)
);
create index on memory_item_sources (source_id);

-- Append-only, like audit_log (0008): the revokes below stop the API roles, this stops everyone else.
create function app.evidence_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '%: source evidence is append-only: % is not permitted', tg_table_name, tg_op;
end $$;
create trigger sources_append_only before update or delete on sources
  for each row execute function app.evidence_append_only();
create trigger sources_no_truncate before truncate on sources
  for each statement execute function app.evidence_append_only();
create trigger memory_item_sources_append_only before update or delete on memory_item_sources
  for each row execute function app.evidence_append_only();
create trigger memory_item_sources_no_truncate before truncate on memory_item_sources
  for each statement execute function app.evidence_append_only();

-- On insert: stamp the time, and fold the new score into the item's aggregate. Rows are immutable,
-- so the running maximum IS the maximum of the item's rows. The update runs inside this trigger
-- (trigger depth 2 at memory_items), which is the only place the guard below admits it.
create function app.memory_item_sources_record() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.computed_at := pg_catalog.now();
  update public.memory_items
     set source_score = greatest(source_score, new.score)
   where id = new.memory_item_id and org_id = new.org_id;
  return new;
end $$;
create trigger memory_item_sources_record before insert on memory_item_sources
  for each row execute function app.memory_item_sources_record();

-- memory_items.source_score is derived. A new item has no sources yet; afterwards the column changes
-- only through the derivation above, never by a direct write from any role.
create function app.memory_items_source_score_derived() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    if new.source_score is not null then
      raise exception 'memory_items.source_score is derived from memory_item_sources';
    end if;
  elsif new.source_score is distinct from old.source_score and pg_catalog.pg_trigger_depth() < 2 then
    raise exception 'memory_items.source_score is derived from memory_item_sources';
  end if;
  return new;
end $$;
create trigger memory_items_source_score_derived before insert or update on memory_items
  for each row execute function app.memory_items_source_score_derived();

-- Backend writes only; members read. Nobody updates, deletes or truncates.
revoke all on sources, memory_item_sources from public, anon, authenticated, service_role;
grant select on sources, memory_item_sources to authenticated, service_role;
grant insert on sources, memory_item_sources to service_role;

alter table sources enable row level security;
alter table memory_item_sources enable row level security;
create policy sources_read on sources for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy memory_item_sources_read on memory_item_sources for select to authenticated
  using (org_id in (select app.member_org_ids()));
