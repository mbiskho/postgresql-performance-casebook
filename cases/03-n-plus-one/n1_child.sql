-- N queries: order.items, lazily loaded for each order.
SELECT id AS item_id, product_id, quantity, unit_price_cents
FROM order_items
WHERE order_id = {{order_id}}
ORDER BY id;
