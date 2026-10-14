-- 0010: correctness fix (Architecture.md §9, stated limitation; Memory.md §6.4).
-- The application roles could delete rows the design says are never deleted: service_role could
-- hard-delete memory_items, and deleting a run or an organisation cascaded into campaigns and
-- approvals, defeating approvals' append-only property. Deleting from these tables is now a
-- deliberate postgres operation (Architecture.md §9, "Deleting an organisation").
-- TRUNCATE is revoked with DELETE: it removes rows without any row-level check.
-- Down: grant delete, truncate on memory_items, runs, campaigns, organisations, org_members
--   to anon, authenticated, service_role;
revoke delete, truncate on memory_items, runs, campaigns, organisations, org_members
  from public, anon, authenticated, service_role;
