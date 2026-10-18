-- Same day, written as a half-open range on the raw column. The index on
-- created_at can now bound the scan.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE created_at >= {{day}}::timestamp AT TIME ZONE 'UTC'
  AND created_at <  ({{day}} + 1)::timestamp AT TIME ZONE 'UTC'
ORDER BY id;
