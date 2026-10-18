-- Keyset pagination: continue strictly after the last row the client saw.
-- The row comparison matches the index column order, so it is a single
-- index condition and the scan starts exactly where the page begins.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE (created_at, id) < ({{cursor_created_at}}, {{cursor_id}})
ORDER BY created_at DESC, id DESC
LIMIT 50;
