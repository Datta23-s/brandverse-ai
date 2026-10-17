-- 0014: every llm_calls row records the prompt template that produced it.
--
-- prompt_id, prompt_version: the versioned template (config/prompts/<id>/<version>.yaml,
--   backend/app/llm/prompts.py) behind the call. Refusals record the prompt they would have sent,
--   and a repair call records the repair template (structured_repair), not the prompt it repairs
--   (that one is named in its route_reason). Without these the evaluation could not show that
--   every run in an arm used the same prompts, and a prompt edited mid-evaluation would silently
--   invalidate the comparison (Rules.md A5, A6). The check is a query of this table
--   (backend/app/evaluation/prompt_versions.py).
--
-- NOT NULL with no default: a call with no recorded prompt cannot enter the raw log (Rules.md
--   A1). Adding a NOT NULL column without a default fails on a table that has rows; llm_calls
--   holds none on the project when this applies (read in the rolled-back dry run before the
--   push), so the failure, if it came, would be loud and complete, never a partial backfill.
-- The formats are the ones the template loader enforces, checked here too so no other writer can
--   enter a value the loader would never produce.
--
-- Grants: unchanged. INSERT and UPDATE were revoked on the whole table from the API roles in
--   0013, which covers new columns; no column-level grant is added. Stated per column in
--   backend/tests/db/test_table_posture.py.
-- No index: the evaluation's read is bounded by its organisations (90 runs x <= 20 calls).
-- Down: alter table llm_calls drop column prompt_version, drop column prompt_id;
alter table llm_calls
  add column prompt_id text not null
    check (prompt_id ~ '^[a-z][a-z0-9_]{0,62}$'),
  add column prompt_version text not null
    check (prompt_version ~ '^v[1-9][0-9]{0,5}$');
