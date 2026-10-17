-- 0020: test organisations are marked by a flag, and every evaluation read excludes them by it.
-- The live project holds rows that are not evaluation data and can never be deleted by
-- application roles (0010, 0017): the two fixed E2E organisations and everything in them,
-- including the lasting run fixture (a brand, a product and a verified claim). Before this, they
-- were kept out of evaluation only by nobody passing their ids to the harness. Now they are
-- flagged and filtered, like the practice organisation (0009) and seed memory (is_seed): the flag,
-- never a remembered id, is the filter, because ids are environment-specific and change on a
-- reseed. Set by the backend only (backend/scripts/ensure_e2e_users.py flags the organisations its
-- own users own): owners' column-level update (0009) does not include it, and the organisation
-- bootstrap (0012) cannot create a flagged organisation.
-- Down: alter policy org_creator_insert on organisations with check (not is_practice);
--   alter table organisations drop column is_test;
alter table organisations add column is_test boolean not null default false;
alter policy org_creator_insert on organisations with check (not is_practice and not is_test);
