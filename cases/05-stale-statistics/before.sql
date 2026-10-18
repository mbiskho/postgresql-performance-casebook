-- Ops dashboard: the 50 most recent delivery exceptions.
SELECT id, order_id, carrier, created_at
FROM shipment_events
WHERE status = 'exception'
ORDER BY created_at DESC
LIMIT 50;
