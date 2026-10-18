CREATE INDEX orders_created_utc_date_idx ON orders (((created_at AT TIME ZONE 'UTC')::date));
-- Expression indexes get their own statistics, gathered by ANALYZE.
ANALYZE orders;
