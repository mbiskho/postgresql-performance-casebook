-- N x M queries: item.product, lazily loaded for each line item.
SELECT id, name, brand
FROM products
WHERE id = {{product_id}};
