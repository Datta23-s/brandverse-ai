-- 0006: marketing memory (Memory.md).
-- Indexes are created without CONCURRENTLY: the table is new and empty here, and CONCURRENTLY
-- cannot run inside a migration transaction. Later indexes on this table must use it (Rules.md §3.3).
-- Down: drop table memory_items;
create table memory_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  brand_id uuid,
  product_id uuid,
  claim_id uuid,                                                          -- E5
  kind text not null,                       -- persona | competitor | insight | claim | creative | sector
  content text not null,
  embedding vector(768),
  embedding_model text,                                                   -- E2
  embedding_version text,                                                 -- E2
  sector text references sectors(id),
  source_url text,
  provenance text not null,                 -- run id, agent, or 'human'
  confidence numeric(3,2),
  freshness_days int,                       -- after which it decays
  approved boolean default false,
  revoked boolean default false,
  revoked_by uuid,                                                        -- E4
  revoked_at timestamptz,                                                 -- E4
  revoked_reason text,                                                    -- E4
  is_seed boolean not null default false,                                 -- E6
  created_at timestamptz default now(),
  last_used_at timestamptz,
  foreign key (brand_id, org_id)   references brands (id, org_id) on delete cascade,               -- E7
  foreign key (product_id, org_id) references products (id, org_id)
    on delete set null (product_id),                                                               -- E7
  foreign key (claim_id, org_id)   references product_claims (id, org_id)
    on delete set null (claim_id),                                                                 -- E5
  check (embedding is null
         or (embedding_model is not null and embedding_version is not null)),
  check (not revoked or (revoked_by is not null and revoked_at is not null))
);

-- As specified in Memory.md §4.3. m and ef_construction equal pgvector's defaults; stated so the
-- migration and the spec cannot drift. The btree serves match_memory's scope (org_id, brand_id,
-- not revoked), which every retrieval applies first (Rules.md P3).
create index on memory_items using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
create index on memory_items using gin (to_tsvector('english', content));
create index on memory_items (org_id, brand_id, kind) where not revoked;

revoke update on memory_items from authenticated;                         -- revocation columns only
grant update (revoked, revoked_by, revoked_at, revoked_reason) on memory_items to authenticated;

alter table memory_items enable row level security;

-- read any role; owner/editor may revoke (column grant above limits what they can set), and only
-- in their own name. (select auth.uid()) is evaluated once per statement, not once per row.
create policy memory_read   on memory_items for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy memory_revoke on memory_items for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']) and revoked_by = (select auth.uid()));
