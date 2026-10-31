-- 0039: a brand's own domains (Phase 6 chunk D, threat V6; owner's ruling 2026-10-26).
--
-- A brand's own site is HIGH authority about its own product and ZERO independence about it, and the
-- system had nowhere to record which sites are a brand's own: `brands` held name, voice, embedding and
-- sector. So `first_party` was a marker nothing could fill, and a claim "verified" against the
-- brand's own page was indistinguishable from independent evidence (V6, docs/evaluation-plan.md).
--
--   brands.domains   the brand's own hostnames, bare, lowercase, no `www.`, at most ten, none
--                    duplicated by a path or a scheme: `{neemans.com, shop.neemans.com}`. Empty means
--                    NONE REGISTERED, which the code reads as "first-party status unknown", never as
--                    "independent" (app/core/first_party.py). A domain left off the list is read as
--                    independent: every domain the brand uses must be listed.
--
-- Like brands.sector (0019) it is a trust-deciding column that cannot be derived: only the brand's
-- owner knows its domains, so the caller supplies it and the server records the act. Every set or
-- change is written to audit_log by an AFTER trigger (so it covers the REST API as well as ours:
-- members can update brands directly, brands_update, 0004), a security-definer function owned by the
-- NOLOGIN role app_audit_writer (insert on audit_log and nothing else), the actor read with
-- auth.uid()'s exact expression, with the same guard 0019 and 0012 use. The writer's one insert policy
-- now admits two actions, `brand.sector_set` and `brand.domains_set`, both on entity_type brand.
--
-- Down: drop trigger brands_audit_domains on brands; drop function app.audit_brand_domains();
--   alter table brands drop column domains; and restore the 0019 policy on audit_log
--   (action = 'brand.sector_set' only).

do $$
begin
  if pg_catalog.regexp_replace(
       (select p.prosrc from pg_catalog.pg_proc p where p.oid = 'auth.uid()'::regprocedure),
       '\s+', '', 'g')
     <> 'selectcoalesce(nullif(current_setting(''request.jwt.claim.sub'',true),''''),(nullif(current_setting(''request.jwt.claims'',true),'''')::jsonb->>''sub''))::uuid'
  then
    raise exception '0039: auth.uid() no longer matches the claim expression app.audit_brand_domains mirrors; review before applying';
  end if;
end $$;

alter table brands
  add column domains text[] not null default '{}',
  add constraint brands_domains_check check (
    cardinality(domains) <= 10
    and (
      domains = '{}'
      or array_to_string(domains, ',') ~
        '^(?!www\.)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?(,(?!www\.)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$'
    )
  );

drop policy audit_writer_insert on audit_log;
create policy audit_writer_insert on audit_log for insert to app_audit_writer
  with check (action in ('brand.sector_set', 'brand.domains_set') and entity_type = 'brand');

create function app.audit_brand_domains() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  -- auth.uid(), expression for expression (see the header and the check above).
  v_user uuid := coalesce(
    nullif(pg_catalog.current_setting('request.jwt.claim.sub', true), ''),
    (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid;
  v_old text[] := case when tg_op = 'UPDATE' then old.domains else '{}'::text[] end;
begin
  if new.domains is not distinct from v_old then
    return null;
  end if;
  insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id, details)
  values (
    new.org_id,
    case when v_user is null then 'service' else 'user' end,
    coalesce(v_user::text, session_user::text),
    'brand.domains_set',
    'brand',
    new.id::text,
    pg_catalog.jsonb_build_object('from', pg_catalog.to_jsonb(v_old), 'to', pg_catalog.to_jsonb(new.domains))
  );
  return null;
end;
$$;

-- Hand the function to its minimal owner (as 0012 and 0019).
grant app_audit_writer to current_user;
grant create on schema app to app_audit_writer;
alter function app.audit_brand_domains() owner to app_audit_writer;
revoke create on schema app from app_audit_writer;
revoke app_audit_writer from current_user;

create trigger brands_audit_domains after insert or update of domains on brands
  for each row execute function app.audit_brand_domains();
