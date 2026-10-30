-- 0037: the persona context of a memory item, for the M-score's customer match C (Phase 6 chunk C;
-- Memory.md section 3.1; owner, 2026-10-29).
--
-- An item's persona context is the persona of the campaign that produced it: the one-line summaries
-- A3 proposed for that run, encoded by the local encoder, stored with the item at write-back.
-- An item that several campaigns reinforced or reused carries several personas, one set per run, and
-- C takes the MAXIMUM over them (and over the request's personas): an item relevant to persona X does
-- not become less relevant to X because it was also used for Y. As with source scores (0028), the SET is
-- stored, not only an aggregate, so the rule can change later without re-encoding anything. Unlike
-- source_score there is no derived column on memory_items: C depends on the request's persona, so it
-- cannot be precomputed, and is taken at retrieval (app/memory/recall.py).
--
--   memory_item_personas   one row per (item, run, persona): the summary, its embedding, and which
--                          encoder made it. A row is only comparable with a request vector made by the
--                          same model and version; retrieval compares like with like and leaves C out
--                          for an item whose personas were made by another.
--
-- Same posture as the evidence tables (the trust-deciding-columns rule, Architecture.md section 9):
-- written by the backend only (no API role holds INSERT or UPDATE), append-only (rows are never updated
-- or deleted, TRUNCATE is trapped), tenant-scoped (org_id, composite foreign keys, members read).
-- Persona rows exist only for items a human-approved campaign touched: they ride with the additions
-- in commit_memory, so a rejected or partial run records none.
--
-- Down: drop table memory_item_personas; drop function app.persona_context_append_only();

create table memory_item_personas (
  memory_item_id uuid not null,
  run_id uuid not null,
  persona_index smallint not null check (persona_index >= 0),   -- position in the run's A3 output
  org_id uuid not null,
  summary text not null check (summary <> '' and char_length(summary) <= 240),
  embedding vector(768) not null,
  embedding_model text not null check (embedding_model <> ''),
  embedding_version text not null check (embedding_version <> ''),
  created_at timestamptz not null default now(),
  primary key (memory_item_id, run_id, persona_index),
  foreign key (memory_item_id, org_id) references memory_items (id, org_id),
  foreign key (run_id, org_id) references runs (id, org_id)
);
create index on memory_item_personas (org_id, memory_item_id);

create function app.persona_context_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '%: persona context is append-only: % is not permitted', tg_table_name, tg_op;
end $$;
create trigger memory_item_personas_append_only before update or delete on memory_item_personas
  for each row execute function app.persona_context_append_only();
create trigger memory_item_personas_no_truncate before truncate on memory_item_personas
  for each statement execute function app.persona_context_append_only();

revoke all on memory_item_personas from public, anon, authenticated, service_role;
grant select on memory_item_personas to authenticated, service_role;
grant insert on memory_item_personas to service_role;

alter table memory_item_personas enable row level security;
create policy memory_item_personas_read on memory_item_personas for select to authenticated
  using (org_id in (select app.member_org_ids()));
