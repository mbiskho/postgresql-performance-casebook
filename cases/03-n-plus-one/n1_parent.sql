-- 1 query: the page's 50 most recent orders.
SELECT id AS order_id, status, total_cents, created_at
FROM orders
WHERE customer_id = {{customer_id}}
ORDER BY created_at DESC
LIMIT 50;
