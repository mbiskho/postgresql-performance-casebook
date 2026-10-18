-- (created_at, id) gives a total order, so the tie-break on id needs no sort
-- and the row comparison in after.sql is one index condition.
CREATE INDEX orders_created_at_id_idx ON orders (created_at, id);

-- The old single-column index is a prefix of the new one.
DROP INDEX orders_created_at_idx;
