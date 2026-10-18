-- Equality columns first, then the sort column, so the index hands back rows
-- already in the order the page wants and the scan stops after 20.
CREATE INDEX orders_customer_status_created_idx
  ON orders (customer_id, status, created_at DESC);

-- (customer_id) is now a prefix of the new index. Any query that used it can
-- use the composite instead, so keeping it only costs write amplification and
-- disk.
DROP INDEX orders_customer_id_idx;
