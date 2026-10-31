-- 0041: one approval decision per campaign round (Phases.md Phase 6 chunk E).
-- A decision must be written once however many times it is submitted: approving twice (a double
-- click, a retried request, a job that runs again after a crash) writes ONE approvals row, not two.
-- The key is (campaign, revision round): a round has at most one decision, and a revision opens the
-- next round. The database enforces it, so no application path can write two.
--
-- `undecided` rows (docs/approval-rubric.md section 5: the approver ran out of the session) are
-- not decisions and stay outside the key, so a later decision in the same round is still possible.
-- A campaign is one row per run (unique run_id), so the park step can upsert it idempotently when
-- a node is re-executed from a checkpoint (Architecture.md section 5.3, at-least-once).
-- Down: alter table approvals drop constraint approvals_one_decision_per_round,
--   drop column revision_round; alter table campaigns drop constraint campaigns_run_id_key,
--   drop column revision_round;
alter table campaigns add column revision_round int not null default 0
  check (revision_round >= 0);
alter table campaigns add constraint campaigns_run_id_key unique (run_id);

alter table approvals add column revision_round int not null default 0
  check (revision_round >= 0);
-- Per APPROVER as well: calibration (rubric section 6) records two scorers' decisions on one
-- campaign, so the key is (campaign, round, approver). One approver cannot decide a round twice.
-- That ONE decision settles an evaluation campaign is not this index's job: it is the application's
-- (app/core/campaigns.py record_decision, which refuses a second approver in an evaluation
-- organisation once the round has a decision).
create unique index approvals_one_decision_per_round
  on approvals (campaign_id, revision_round, approver_id) where outcome <> 'undecided';
