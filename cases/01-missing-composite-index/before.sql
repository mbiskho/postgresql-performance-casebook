-- "Open orders" panel on the account page: the 20 most recent orders that
-- are still in flight.
SELECT id, status, total_cents, created_at
FROM orders
WHERE customer_id = {{customer_id}}
  AND status IN ('pending', 'paid', 'shipped')
ORDER BY created_at DESC
LIMIT 20;
