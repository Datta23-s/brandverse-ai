-- 0004: brands, products and the product truth store.
-- Down: drop table product_claims; drop table products; drop table brands;
create table brands (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,
  name text not null,
  voice_guidelines text,
  voice_embedding vector(768),
  voice_embedding_model text,                                     -- E3
  voice_embedding_version text,                                   -- E3
  sector text references sectors(id),                             -- registry key; no free text
  created_at timestamptz default now(),
  unique (id, org_id),
  check (voice_embedding is null
         or (voice_embedding_model is not null and voice_embedding_version is not null))
);

create table products (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  brand_id uuid not null,
  name text not null,
  category text,
  attributes jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  unique (id, org_id),
  foreign key (brand_id, org_id) references brands (id, org_id) on delete cascade
);

-- The product truth store: the spine of anti-hallucination
create table product_claims (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organisations(id) on delete cascade,   -- E1
  product_id uuid not null,
  claim text not null,
  claim_type text not null check (claim_type in ('verified','deduced','restricted')),
  evidence_url text,
  evidence_note text,
  confidence numeric(3,2) check (confidence between 0 and 1),
  created_by text not null,                 -- 'human' | agent id
  created_at timestamptz default now(),
  unique (id, org_id),                                                    -- target for E5
  foreign key (product_id, org_id) references products (id, org_id) on delete cascade
);

alter table brands         enable row level security;
alter table products       enable row level security;
alter table product_claims enable row level security;

-- read any role; write owner/editor; delete owner
create policy brands_read   on brands for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy brands_insert on brands for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy brands_update on brands for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy brands_delete on brands for delete to authenticated
  using (app.has_role(org_id, array['owner']));

create policy products_read   on products for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy products_insert on products for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy products_update on products for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy products_delete on products for delete to authenticated
  using (app.has_role(org_id, array['owner']));

create policy claims_read   on product_claims for select to authenticated
  using (org_id in (select app.member_org_ids()));
create policy claims_insert on product_claims for insert to authenticated
  with check (app.has_role(org_id, array['owner','editor']));
create policy claims_update on product_claims for update to authenticated
  using (app.has_role(org_id, array['owner','editor']))
  with check (app.has_role(org_id, array['owner','editor']));
create policy claims_delete on product_claims for delete to authenticated
  using (app.has_role(org_id, array['owner']));
