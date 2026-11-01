-- 0043: anon and authenticated hold no write privilege on campaigns, and no INSERT on approvals
-- (Architecture.md section 9; owner, 2026-11-01).
--
-- Same class as 0029 and 0030 for memory_items. Since 0007 and 0009 the API roles have held
-- Supabase's default table-level INSERT and UPDATE on campaigns, and INSERT on approvals, so every
-- column, `campaigns.status`, `approved_by` and `approvals.outcome` included. They were blocked only
-- by row-level security (no INSERT or UPDATE policy for them) and the approver trigger, not by the
-- grant: one policy added in error away from a user approving their own campaign. Owner,
-- 2026-10-26, for memory: make the exemption impossible rather than defended in depth. Both tables
-- are written by the backend (service_role) alone: the pipeline parks the campaign, the approval API
-- records the decision (app/core/campaigns.py).
--
-- Scope, stated: INSERT and UPDATE on campaigns, INSERT on approvals, for anon and authenticated.
-- Not touched: SELECT (the members' read policies), REFERENCES, TRIGGER, UPDATE and DELETE on
-- approvals (already revoked by 0009), and everything service_role holds.
--
-- Down: grant insert, update on campaigns to anon, authenticated;
--   grant insert on approvals to anon, authenticated;
revoke insert, update on campaigns from anon, authenticated;
revoke insert on approvals from anon, authenticated;
