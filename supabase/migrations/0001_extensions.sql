-- 0001: extensions and the app helper schema.
-- Down (destructive; only on a database with no BrandverseAI data):
--   drop schema app cascade; drop extension pg_trgm; drop extension vector;
create extension if not exists vector;
create extension if not exists pg_trgm;
create schema if not exists app;
-- RLS policies call app.member_org_ids() and app.has_role() as the caller's role, so the API
-- roles need USAGE.
grant usage on schema app to anon, authenticated, service_role;
