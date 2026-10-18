CREATE INDEX orders_open_by_customer_idx ON orders (customer_id, created_at DESC)
  WHERE status IN ('pending', 'paid', 'shipped');
