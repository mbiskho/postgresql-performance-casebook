-- No schema change. The fix is the rewrite in after.sql: the predicate now
-- compares the indexed column directly, so orders_created_at_idx applies.
--
-- An expression index on ((created_at AT TIME ZONE 'UTC')::date) would also
-- work, but it costs ~300 MB and slows every insert to fix one query that
-- can be rewritten for free.
SELECT 1;
