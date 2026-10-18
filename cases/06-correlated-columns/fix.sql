-- Tell the planner the columns move together. `dependencies` records that
-- country determines currency and locale; `mcv` stores the most common
-- combinations with their real frequencies, which is what fixes this
-- estimate (SG/SGD/en-SG/Asia/Singapore is one combination, ~2% of rows).
CREATE STATISTICS customers_geo_stats (dependencies, mcv)
  ON country, currency, locale, timezone FROM customers;

-- Extended statistics are only populated by ANALYZE.
ANALYZE customers;
