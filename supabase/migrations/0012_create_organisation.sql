-- 0012: a signed-in user creates an organisation and becomes its owner.
--
-- Before this, a new user had no way to their first organisation: `organisations` has no insert
-- policy, and adding a member needs an existing owner. app.create_organisation is that way.
--
-- 1. NAME ONLY. The owner is the caller, derived inside from the request's JWT claims. A user-id
--    parameter would let any signed-in caller mint an owner membership for someone else.
-- 2. search_path pinned, as on every app.* function.
-- 3. Security definer with the minimum rights needed. The function is owned by app_org_creator,
--    a NOLOGIN role holding INSERT on organisations and org_members and nothing else: no SELECT,
--    UPDATE, DELETE or TRUNCATE anywhere, no BYPASSRLS. Row-level security still binds it; its
--    two insert policies admit only the rows this function writes. No API role is a member of it.
--
-- Why the caller is not read with auth.uid(): Supabase's postgres role cannot grant USAGE on
-- schema auth (checked 2026-10-16: no grant option), so app_org_creator cannot call it. The
-- function reads the same claims with the same expression instead, and this migration refuses to
-- apply on a platform whose auth.uid() differs from that expression; a database test asserts the
-- two agree for every claim shape (backend/tests/db/test_create_organisation.py).
--
-- Accepted limitation (2026-10-16): nothing limits how many organisations one user creates.
-- Down: drop function app.create_organisation(text);
--   drop policy org_creator_insert on organisations; drop policy members_creator_insert on org_members;
--   revoke all on organisations, org_members from app_org_creator;
--   revoke usage on schema public, app from app_org_creator; drop role app_org_creator;

-- The expression below must be auth.uid()'s, exactly (whitespace aside).
do $$
begin
  if pg_catalog.regexp_replace(
       (select p.prosrc from pg_catalog.pg_proc p where p.oid = 'auth.uid()'::regprocedure),
       '\s+', '', 'g')
     <> 'selectcoalesce(nullif(current_setting(''request.jwt.claim.sub'',true),''''),(nullif(current_setting(''request.jwt.claims'',true),'''')::jsonb->>''sub''))::uuid'
  then
    raise exception '0012: auth.uid() no longer matches the claim expression app.create_organisation mirrors; review before applying';
  end if;
end $$;

-- Roles are cluster-wide; create once (a re-created test database meets an existing role).
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'app_org_creator') then
    create role app_org_creator nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;

grant usage on schema public, app to app_org_creator;
grant insert on organisations, org_members to app_org_creator;

create policy org_creator_insert on organisations for insert to app_org_creator
  with check (not is_practice);
create policy members_creator_insert on org_members for insert to app_org_creator
  with check (role = 'owner' and granted_by = user_id and revoked_at is null);

create function app.create_organisation(p_name text) returns uuid
language plpgsql volatile security definer set search_path = '' as $$
declare
  -- auth.uid(), expression for expression (see the header and the check above).
  v_owner uuid := coalesce(
    nullif(pg_catalog.current_setting('request.jwt.claim.sub', true), ''),
    (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid;
  v_name text := pg_catalog.btrim(p_name);
  v_org uuid := pg_catalog.gen_random_uuid();
begin
  if v_owner is null then
    raise exception 'create_organisation: not signed in' using errcode = '42501';
  end if;
  if v_name is null or pg_catalog.char_length(v_name) not between 1 and 120 then
    raise exception 'create_organisation: name must be 1 to 120 characters' using errcode = '22023';
  end if;
  insert into public.organisations (id, name) values (v_org, v_name);
  insert into public.org_members (org_id, user_id, role, granted_by)
    values (v_org, v_owner, 'owner', v_owner);
  return v_org;
end;
$$;

-- Callable by signed-in users only (functions are executable by PUBLIC by default, and Supabase
-- grants the API roles more). Settled BEFORE the owner changes: afterwards the migration role is
-- no longer the owner, and its REVOKE would silently match nothing (found in a rolled-back dry
-- run on the project, 2026-10-16; a superuser, as in CI, does not show it).
revoke all on function app.create_organisation(text) from public, anon, service_role;
grant execute on function app.create_organisation(text) to authenticated;

-- Hand the function to its minimal owner. Changing owner needs SET ROLE on the new owner and
-- CREATE on the schema; both are granted for the change only. (On PostgreSQL 16+ the role's
-- creator keeps the automatic ADMIN membership; postgres already holds every right the role has.)
grant app_org_creator to current_user;
grant create on schema app to app_org_creator;
alter function app.create_organisation(text) owner to app_org_creator;
revoke create on schema app from app_org_creator;
revoke app_org_creator from current_user;
