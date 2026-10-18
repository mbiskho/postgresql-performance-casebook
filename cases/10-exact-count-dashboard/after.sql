-- Same answer: closed days from the rollup, today counted live. The live
-- part is an index range scan over one day of orders.
SELECT status, sum(orders)::bigint AS orders, sum(revenue_cents)::numeric AS revenue_cents
FROM (
    SELECT status, orders, revenue_cents
    FROM daily_order_stats
    UNION ALL
    SELECT status, count(*), sum(total_cents)
    FROM orders
    WHERE created_at >= {{rollup_through}}
    GROUP BY status
) s
GROUP BY status
ORDER BY status;
