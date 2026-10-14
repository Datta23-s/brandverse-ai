-- 0011: correctness fix, completing 0010 (Architecture.md §9, stated limitation).
-- 1. llm_calls are the evaluation's raw logs (Rules.md A1); sector_constraints rows are retired,
--    never deleted, and their trigger fires on DELETE only, not TRUNCATE; sectors are the keys the
--    registry and brands depend on. No application role deletes or truncates any of them.
-- 2. TRUNCATE ignores row-level security; Supabase's default privileges grant it to every API
--    role on every new table. Revoked on the remaining tables, so no application role holds it
--    anywhere (backend/tests/db/test_table_posture.py states the posture of every table).
-- 3. memory_items.brand_id becomes ON DELETE NO ACTION: deleting a brand no longer hard-deletes
--    its memory (Memory.md §6.4). A brand with memory items cannot be deleted — the same rule as a
--    product with runs. Deleting the whole organisation (a postgres procedure) still works: the
--    cascade removes its memory items before the check runs at the end of the statement.
-- Down: grant delete, truncate on llm_calls, sector_constraints, sectors to anon, authenticated,
--   service_role; grant truncate on brands, products, product_claims to anon, authenticated,
--   service_role; re-create memory_items_brand_id_org_id_fkey with ON DELETE CASCADE.
revoke delete, truncate on llm_calls, sector_constraints, sectors
  from public, anon, authenticated, service_role;
revoke truncate on brands, products, product_claims
  from public, anon, authenticated, service_role;

alter table memory_items
  drop constraint memory_items_brand_id_org_id_fkey,
  add constraint memory_items_brand_id_org_id_fkey
    foreign key (brand_id, org_id) references brands (id, org_id);
