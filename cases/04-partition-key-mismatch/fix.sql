-- Re-partition by time: one partition per calendar month (UTC).
CREATE TABLE page_views_by_month (
    user_id     bigint      NOT NULL,
    viewed_at   timestamptz NOT NULL,
    path        text        NOT NULL,
    referrer    text,
    duration_ms integer     NOT NULL
) PARTITION BY RANGE (viewed_at);

DO $$
DECLARE
  m date := date '2025-04-01';
BEGIN
  WHILE m < date '2026-11-01' LOOP
    EXECUTE format(
      'CREATE TABLE page_views_m%s PARTITION OF page_views_by_month
         FOR VALUES FROM (%L) TO (%L)',
      to_char(m, 'YYYY_MM'), m::timestamp AT TIME ZONE 'UTC',
      (m + interval '1 month')::timestamp AT TIME ZONE 'UTC');
    m := m + interval '1 month';
  END LOOP;
END $$;

-- Copy in time order, so each month's heap is append-ordered by viewed_at.
INSERT INTO page_views_by_month
SELECT * FROM page_views ORDER BY viewed_at;

-- Keep the per-user feed working: a local (user_id, viewed_at) index on each
-- month. Built after the load, which is much faster than maintaining it row by
-- row during the copy.
CREATE INDEX page_views_by_month_user_viewed_idx ON page_views_by_month (user_id, viewed_at);
VACUUM (ANALYZE) page_views_by_month;

-- Swap. In production this is the last step of an online migration
-- (dual-write or logical replication into the new table, then a short lock
-- for the rename); here the data is static, so a copy is enough.
ALTER TABLE page_views RENAME TO page_views_by_user_hash;
ALTER TABLE page_views_by_month RENAME TO page_views;
