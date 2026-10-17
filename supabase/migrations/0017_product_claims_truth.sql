-- 0017: the product truth store's definitions become constraints, and its provenance is derived by
-- the server (Phases.md Phase 3; Rules.md T1, T2, P9).
--
-- 1. What each claim_type means, as CHECKs a test can assert (Architecture.md §8.2):
--    verified   — entered by a signed-in human, with an http(s) evidence URL. No agent row is ever
--                 verified.
--    deduced    — carries a confidence and a basis: an evidence URL or a written note.
--    restricted — carries the reason it is restricted (evidence_note). A property of the statement,
--                 not of its evidence: a manufacturer saying "unbeatable" does not make it sayable.
-- 2. Provenance from the authenticated identity, never from the caller (Architecture.md §9,
--    "Trust-deciding columns"). Members can insert product_claims through the REST API directly
--    (claims_insert, 0004), so a caller-supplied created_by = 'human' would forge a verified claim
--    past every API check. The trigger overwrites created_by, entered_by and created_at for the API
--    roles: created_by = 'human', entered_by = auth.uid(). The backend's own role (agents) may not
--    write 'human' at all.
-- 3. Claims are immutable: never edited, never deleted; a wrong claim is retired, with who, when
--    and why recorded in the row (retired_by = auth.uid() and retired_at = now(), both set by the
--    trigger; retired_reason from the caller, required). A claim a campaign cited cannot change
--    under it, so the campaign's source trail stays true — the reasoning of append-only approvals
--    (0009) and Memory.md §6.4's provenance chain. Deleting the product still cascades to its claims
--    (a foreign-key action, not a grant); a product with runs cannot be deleted (0005).
--    Binds the application roles, not the table owner (§9, stated limitation).
-- The project held no product_claims rows when this was written.
-- Down: drop trigger product_claims_provenance on product_claims;
--   drop function app.product_claims_provenance();
--   alter table product_claims drop constraint product_claims_claim_text,
--     drop constraint product_claims_human_is_a_user, drop constraint product_claims_evidence_url,
--     drop constraint product_claims_verified, drop constraint product_claims_deduced,
--     drop constraint product_claims_restricted, drop constraint product_claims_retirement,
--     drop column entered_by, drop column retired_at, drop column retired_by,
--     drop column retired_reason;
--   create policy claims_delete on product_claims for delete to authenticated
--     using (app.has_role(org_id, array['owner']));
--   grant delete on product_claims to anon, authenticated, service_role;
alter table product_claims
  add column entered_by uuid references auth.users(id),
  add column retired_at timestamptz,
  add column retired_by uuid references auth.users(id),
  add column retired_reason text,
  add constraint product_claims_claim_text
    check (length(btrim(claim)) between 1 and 500),
  add constraint product_claims_human_is_a_user
    check ((created_by = 'human') = (entered_by is not null)),
  add constraint product_claims_evidence_url
    check (evidence_url is null or evidence_url ~ '^https?://[^[:space:]]+$'),
  add constraint product_claims_verified
    check (claim_type <> 'verified' or (created_by = 'human' and evidence_url is not null)),
  add constraint product_claims_deduced
    check (claim_type <> 'deduced'
           or (confidence is not null
               and (evidence_url is not null or nullif(btrim(evidence_note), '') is not null))),
  add constraint product_claims_restricted
    check (claim_type <> 'restricted' or nullif(btrim(evidence_note), '') is not null),
  add constraint product_claims_retirement
    check ((retired_at is null) = (retired_by is null)
           and (retired_at is null) = (retired_reason is null)
           and (retired_reason is null or btrim(retired_reason) <> ''));

create function app.product_claims_provenance() returns trigger
language plpgsql set search_path = '' as $$
declare
  uid uuid;
  retirement constant text[] := array['retired_at', 'retired_by', 'retired_reason'];
begin
  if tg_op = 'INSERT' then
    if current_user in ('authenticated', 'anon') then
      uid := (select auth.uid());
      if uid is null then
        raise exception 'product_claims: a claim is entered by a signed-in user';
      end if;
      new.created_by := 'human';
      new.entered_by := uid;
    elsif new.created_by = 'human' or new.entered_by is not null then
      raise exception 'product_claims: only a signed-in user enters a human claim';
    end if;
    if new.retired_at is not null or new.retired_by is not null or new.retired_reason is not null then
      raise exception 'product_claims: a claim is entered active and retired afterwards';
    end if;
    new.created_at := pg_catalog.now();
    return new;
  end if;

  -- UPDATE: retirement, once, by a signed-in user. Nothing else ever changes.
  if old.retired_at is not null then
    raise exception 'product_claims: a retired claim cannot change';
  end if;
  if (pg_catalog.to_jsonb(new) - retirement) is distinct from (pg_catalog.to_jsonb(old) - retirement) then
    raise exception 'product_claims: claims are never edited; retire it and enter a new claim';
  end if;
  if new.retired_at is null and new.retired_by is null and new.retired_reason is null then
    return new;
  end if;
  if current_user <> 'authenticated' then
    raise exception 'product_claims: a claim is retired by a signed-in user';
  end if;
  uid := (select auth.uid());
  if uid is null then
    raise exception 'product_claims: a claim is retired by a signed-in user';
  end if;
  new.retired_at := pg_catalog.now();
  new.retired_by := uid;
  return new;
end $$;
create trigger product_claims_provenance before insert or update on product_claims
  for each row execute function app.product_claims_provenance();

drop policy claims_delete on product_claims;
revoke delete on product_claims from public, anon, authenticated, service_role;
