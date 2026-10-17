-- 0019: every time a brand's sector is set or changed, audit_log records who, when, from what and
-- to what (Architecture.md §8.1). The sector decides which registry rules bind a brand's output and
-- whether the run must clear the regulated-sector gates, so it is a trust-deciding column. Unlike
-- created_by on claims it cannot be derived — only the brand's owner knows its sector — so the
-- caller supplies it and the server records the act, with the actor taken from the authenticated
-- identity (Architecture.md §9, "Trust-deciding columns").
--
-- An AFTER trigger, so it covers the REST API as well as ours: members can update brands directly
-- (brands_update, 0004). The trigger function is security definer, owned by app_audit_writer, a
-- NOLOGIN role holding INSERT on audit_log and nothing else, bound by one insert policy that admits
-- only this function's rows. API roles hold no INSERT on audit_log (0008) and are not members of
-- the role. A trigger function cannot be called directly, so no caller can write an audit row
-- through it except by really changing a sector.
--
-- The actor is read with auth.uid()'s exact expression, not auth.uid() itself: the owner role
-- cannot be granted USAGE on schema auth on Supabase (0012, header). 0012 refuses to apply if the
-- expression and auth.uid() differ, and the same check is repeated here.
-- Down: drop trigger brands_audit_sector on brands; drop function app.audit_brand_sector();
--   drop policy audit_writer_insert on audit_log; revoke insert on audit_log from app_audit_writer;
--   revoke usage on schema public, app from app_audit_writer; drop role app_audit_writer;

do $$
begin
  if pg_catalog.regexp_replace(
       (select p.prosrc from pg_catalog.pg_proc p where p.oid = 'auth.uid()'::regprocedure),
       '\s+', '', 'g')
     <> 'selectcoalesce(nullif(current_setting(''request.jwt.claim.sub'',true),''''),(nullif(current_setting(''request.jwt.claims'',true),'''')::jsonb->>''sub''))::uuid'
  then
    raise exception '0019: auth.uid() no longer matches the claim expression app.audit_brand_sector mirrors; review before applying';
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'app_audit_writer') then
    create role app_audit_writer nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;

grant usage on schema public, app to app_audit_writer;
grant insert on audit_log to app_audit_writer;

create policy audit_writer_insert on audit_log for insert to app_audit_writer
  with check (action = 'brand.sector_set' and entity_type = 'brand');

create function app.audit_brand_sector() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  -- auth.uid(), expression for expression (see the header and the check above).
  v_user uuid := coalesce(
    nullif(pg_catalog.current_setting('request.jwt.claim.sub', true), ''),
    (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid;
  v_old text := case when tg_op = 'UPDATE' then old.sector end;
begin
  if new.sector is not distinct from v_old then
    return null;
  end if;
  insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id, details)
  values (
    new.org_id,
    case when v_user is null then 'service' else 'user' end,
    coalesce(v_user::text, session_user::text),
    'brand.sector_set',
    'brand',
    new.id::text,
    pg_catalog.jsonb_build_object('from', v_old, 'to', new.sector)
  );
  return null;
end;
$$;

-- Hand the function to its minimal owner (as 0012: SET ROLE and CREATE for the change only).
grant app_audit_writer to current_user;
grant create on schema app to app_audit_writer;
alter function app.audit_brand_sector() owner to app_audit_writer;
revoke create on schema app from app_audit_writer;
revoke app_audit_writer from current_user;

create trigger brands_audit_sector after insert or update of sector on brands
  for each row execute function app.audit_brand_sector();
