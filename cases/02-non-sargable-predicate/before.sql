-- Finance export: every order placed on a given (UTC) calendar day.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE (created_at AT TIME ZONE 'UTC')::date = {{day}}
ORDER BY id;
