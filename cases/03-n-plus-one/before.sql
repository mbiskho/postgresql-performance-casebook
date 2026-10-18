-- The N+1 page is driven from Python (casebook/measure.py, measure_n_plus_one)
-- using n1_parent.sql, n1_child.sql and n1_grandchild.sql. This file is the
-- parent query, kept so every case has a before.sql.
SELECT id AS order_id, status, total_cents, created_at
FROM orders
WHERE customer_id = {{customer_id}}
ORDER BY created_at DESC
LIMIT 50;
