-- Finance export: every order placed on a given (UTC) calendar day.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE (created_at AT TIME ZONE 'UTC')::date = '2026-03-14'::date
ORDER BY id;
