-- The rollup a scheduled job maintains. Statuses change for ~14 days after an
-- order is placed, so the nightly job recomputes the trailing 14 days and
-- leaves older days alone. This statement is the job's one-off backfill.
CREATE TABLE daily_order_stats (
    day           date   NOT NULL,
    status        text   NOT NULL,
    orders        bigint NOT NULL,
    revenue_cents bigint NOT NULL,
    PRIMARY KEY (day, status)
);

INSERT INTO daily_order_stats
SELECT (created_at AT TIME ZONE 'UTC')::date, status, count(*), sum(total_cents)
FROM orders
WHERE created_at < {{rollup_through}}
GROUP BY 1, 2;

ANALYZE daily_order_stats;
