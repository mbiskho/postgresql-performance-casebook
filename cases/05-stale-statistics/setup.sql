-- Replays the load history that leaves shipment_events with stale statistics.
-- Rebuilt from scratch on every run so the broken state is exact.
DROP TABLE IF EXISTS shipment_events;

-- The import job switched autovacuum off for the table to speed up the
-- backfill and nobody switched it back on. With it on, autoanalyze would
-- eventually fix this; the case is about the window before it does.
CREATE TABLE shipment_events (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id   bigint      NOT NULL,
    carrier    text        NOT NULL,
    status     text        NOT NULL,
    created_at timestamptz NOT NULL
) WITH (autovacuum_enabled = false);

CREATE INDEX shipment_events_status_idx     ON shipment_events (status);
CREATE INDEX shipment_events_created_at_idx ON shipment_events (created_at);

-- Go-live day: the carrier integration mapped unknown codes to 'exception',
-- so 40% of the first 2,000 events were exceptions.
SELECT setseed(0.05);
INSERT INTO shipment_events (order_id, carrier, status, created_at)
SELECT 1 + floor(random() * 1000000)::bigint,
       (ARRAY['JNE', 'SiCepat', 'DHL', 'FedEx'])[1 + floor(random() * 4)::int],
       CASE WHEN random() < 0.40 THEN 'exception'
            ELSE (ARRAY['label_created', 'picked_up', 'in_transit', 'delivered'])[1 + floor(random() * 4)::int] END,
       timestamptz '2026-04-01 00:00:00+00' + interval '1 day' * (g / 2000.0)
FROM generate_series(1, 2000) g;

-- Someone looked at the table that day and ran ANALYZE. The statistics now
-- say status = 'exception' matches ~40% of rows.
ANALYZE shipment_events;

-- Six months of normal operation, appended without another ANALYZE.
-- Exceptions are now genuinely rare (about 1 in 50,000 events).
INSERT INTO shipment_events (order_id, carrier, status, created_at)
SELECT o.id,
       (ARRAY['JNE', 'SiCepat', 'DHL', 'FedEx'])[1 + floor(random() * 4)::int],
       CASE WHEN random() < 0.00002 THEN 'exception'
            ELSE (ARRAY['label_created', 'picked_up', 'in_transit', 'delivered'])[1 + floor(random() * 4)::int] END,
       timestamptz '2026-04-02 00:00:00+00'
         + (timestamptz '2026-09-30 23:59:59+00' - timestamptz '2026-04-02 00:00:00+00')
           * ((row_number() OVER (ORDER BY o.id)) / (count(*) OVER ())::float8)
FROM orders o
WHERE o.id % 3 = 0;
