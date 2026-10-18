CREATE INDEX IF NOT EXISTS orders_customer_id_idx ON orders (customer_id);
DROP INDEX IF EXISTS orders_customer_status_created_idx;
