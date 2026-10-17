-- TEST-ONLY. Recreates the parts of the Supabase platform that supabase/migrations depend on,
-- so the migrations (and the RLS policies in them) run byte-identical to what deploys.
-- Never applied to a real Supabase project, which already provides all of this.

-- API roles. Supabase's service_role bypasses RLS.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $$;

grant usage on schema public to anon, authenticated, service_role;

-- Supabase's default grants on objects created in public by the migration role.
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;

create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;

-- Minimal auth.users: the migrations only reference users(id); email (as on Supabase) is read by
-- backend/scripts/flag_test_organisation.py, which finds an organisation through its owner.
create table if not exists auth.users (id uuid primary key, email text);

-- Verbatim from supabase/auth migrations/20220224000811_update_auth_functions.up.sql
-- (template namespace = auth), the latest supabase/auth migration that defines uid(); checked
-- against master on 2026-10-14. No Supabase project exists yet to read it from.
-- TODO(project creation): confirm against the instance with
--   select pg_get_functiondef('auth.uid'::regprocedure);
-- and update this copy if it differs.
create or replace function auth.uid()
returns uuid
language sql stable
as $$
  select
  coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

-- Verbatim from supabase/auth migrations/20220531120530_add_auth_jwt_function.up.sql
create or replace function auth.jwt()
returns jsonb
language sql stable
as $$
  select
    coalesce(
        nullif(current_setting('request.jwt.claim', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')
    )::jsonb
$$;
