-- Admin dashboard header, run on every page view.
SELECT status, count(*) AS orders, sum(total_cents) AS revenue_cents
FROM orders
GROUP BY status
ORDER BY status;
