-- 0018: the sector keys brands.sector references (Architecture.md §8.1), seeded from the sectors
-- the evaluation and tuning sets use (evaluation/test_set.yaml, evaluation/tuning_brands.yaml).
-- `regulated` is copied from those files: health supplements is the one regulated sector (Rules.md
-- T4). Keys only — no sector_constraints rows. Registry rules are written by a human from the
-- regulation they cite (§8.1, Authorship), never seeded here and never generated; until a regulated
-- sector has active rules, every run for a brand in it is blocked (fail closed). Phases.md records
-- that as a human prerequisite of Phase 10. The project's sectors table was empty when this was
-- written.
-- Down: delete from sectors where id in ('consumer-electronics', 'travel-goods', 'footwear',
--   'personal-care', 'food-and-beverage', 'health-supplements', 'kitchenware', 'home-furnishing',
--   'home-appliances');  -- only while no brand, memory item or registry row references them
insert into sectors (id, label, regulated) values
  ('consumer-electronics', 'Consumer electronics', false),
  ('travel-goods',         'Travel goods',         false),
  ('footwear',             'Footwear',             false),
  ('personal-care',        'Personal care',        false),
  ('food-and-beverage',    'Food and beverage',    false),
  ('health-supplements',   'Health supplements',   true),
  ('kitchenware',          'Kitchenware',          false),
  ('home-furnishing',      'Home furnishing',      false),
  ('home-appliances',      'Home appliances',      false);
