CREATE INDEX IF NOT EXISTS orders_created_at_idx ON orders (created_at);
DROP INDEX IF EXISTS orders_created_at_id_idx;
