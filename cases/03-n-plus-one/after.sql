-- One round trip: the same page as a single statement. Items and products
-- are joined and folded into one JSON array per order, which is the shape the
-- page template wanted all along.
SELECT o.id AS order_id, o.status, o.total_cents, o.created_at,
       json_agg(json_build_object(
           'item_id', i.id, 'product_id', i.product_id, 'name', p.name, 'brand', p.brand,
           'quantity', i.quantity, 'unit_price_cents', i.unit_price_cents)
         ORDER BY i.id) AS items
FROM (
    SELECT id, status, total_cents, created_at
    FROM orders
    WHERE customer_id = {{customer_id}}
    ORDER BY created_at DESC
    LIMIT 50
) o
JOIN order_items i ON i.order_id = o.id
JOIN products p    ON p.id = i.product_id
GROUP BY o.id, o.status, o.total_cents, o.created_at
ORDER BY o.created_at DESC;
