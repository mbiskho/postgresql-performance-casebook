-- 1 query: the page's 50 most recent orders.
SELECT id AS order_id, status, total_cents, created_at
FROM orders
WHERE customer_id = 1021
ORDER BY created_at DESC
LIMIT 50;
