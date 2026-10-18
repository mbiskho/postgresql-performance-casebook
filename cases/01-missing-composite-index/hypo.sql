-- Ask the planner whether it would use the index before paying to build it.
SELECT * FROM hypopg_create_index(
  'CREATE INDEX ON orders (customer_id, status, created_at DESC)');
