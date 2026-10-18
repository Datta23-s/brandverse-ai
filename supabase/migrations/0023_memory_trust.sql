-- 0023: closes the two memory instances of the trust-deciding-columns rule left open in Phase 3
-- (Architecture.md §9, "Known remaining instances"):
--
-- 1. Revocation is recorded by the server. Before this, a revoking user supplied revoked_at
--    themself (memory_revoke checks only revoked_by = auth.uid()), so a revocation could be
--    back-dated. Now a trigger sets revoked_at = now() — and, for a signed-in user, revoked_by =
--    auth.uid() — whatever was sent. Revocation is also made one-way: Memory.md §6.4 says a revoked
--    item leaves retrieval permanently, so revoked cannot return to false, and a recorded
--    revocation (who, when, why) cannot be rewritten. The backend's own role may revoke (Phase 4's
--    confidence decay) and must name the revoker; the table's check constraint still requires it.
-- 2. A memory item backed by a claim keeps its claim. memory_items.claim_id was ON DELETE SET NULL,
--    and deleting a product with no runs cascades to its claims, so a claim-backed item would have
--    lost its provenance silently. The foreign key becomes NO ACTION: a product whose claims back a
--    memory item cannot be deleted, as a product with runs cannot (0005), and a brand with memory
--    items cannot (0011). The project held no memory_items rows when this was written.
-- Down: drop trigger memory_items_revocation on memory_items;
--   drop function app.memory_items_revocation();
--   alter table memory_items drop constraint memory_items_claim_id_org_id_fkey,
--     add constraint memory_items_claim_id_org_id_fkey foreign key (claim_id, org_id)
--       references product_claims (id, org_id) on delete set null (claim_id);
create function app.memory_items_revocation() returns trigger
language plpgsql set search_path = '' as $$
begin
  if old.revoked and not new.revoked then
    raise exception 'memory_items: revocation is permanent (Memory.md §6.4)';
  end if;
  if old.revoked then
    if (new.revoked_by, new.revoked_at, new.revoked_reason)
       is distinct from (old.revoked_by, old.revoked_at, old.revoked_reason) then
      raise exception 'memory_items: a revocation is recorded once and never rewritten';
    end if;
    return new;
  end if;
  if new.revoked then
    new.revoked_at := pg_catalog.now();
    if current_user in ('authenticated', 'anon') then
      new.revoked_by := (select auth.uid());
    end if;
  end if;
  return new;
end $$;
create trigger memory_items_revocation before update on memory_items
  for each row execute function app.memory_items_revocation();

alter table memory_items
  drop constraint memory_items_claim_id_org_id_fkey,
  add constraint memory_items_claim_id_org_id_fkey foreign key (claim_id, org_id)
    references product_claims (id, org_id);
