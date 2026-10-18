-- Admin "all orders" list, newest first, 50 per page; this is page 4,001.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
ORDER BY created_at DESC, id DESC
OFFSET {{offset}}
LIMIT 50;
