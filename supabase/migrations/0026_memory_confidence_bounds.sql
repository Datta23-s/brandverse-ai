-- 0026: a memory item's confidence is a probability, and a live item is never below the
-- revocation threshold (Memory.md §6.3).
--
-- Two gaps 0025 left open. 0025 made confidence required but not bounded, so (1) a value outside
-- [0, 1] was storable, and the decrement could compute one (0.10 - 0.15); and (2) an item could be
-- written live below 0.30. §6.3 revokes an item that falls below 0.30, but the rule ran only when
-- a decrement happened: an item born at 0.20 was live and retrievable until some correction found
-- it — exempt from the mechanism meant to cover every item, by how it entered. Decision
-- 2026-10-25 (project owner): revocation tests the VALUE, not the transition, and the database
-- enforces it, so no path can write the exception.
--
--   memory_items_confidence_range  0 <= confidence <= 1
--   memory_items_live_confidence   revoked, or confidence >= 0.30
--
-- 0.30 is config/scoring.yaml writeback.revoke_below; test_memory_corrections.py fails if the two
-- differ. Raising the threshold therefore needs a migration that first revokes the items it would
-- strand. The code that lowers confidence (write-back's _correct) clamps at 0 and sets `revoked`
-- in the same statement as the value, so the second constraint holds at every statement. The
-- project held no memory_items rows when this was written.
--
-- Down: alter table memory_items drop constraint memory_items_confidence_range,
--   drop constraint memory_items_live_confidence;
alter table memory_items
  add constraint memory_items_confidence_range check (confidence >= 0 and confidence <= 1),
  add constraint memory_items_live_confidence check (revoked or confidence >= 0.30);
