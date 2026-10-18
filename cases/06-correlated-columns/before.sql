-- Finance: Q3 revenue from Singapore customers, by loyalty tier. The filter
-- comes from a saved "Singapore" segment that pins every geo column.
SELECT c.tier, count(*) AS orders, sum(o.total_cents) AS revenue_cents
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE c.country  = 'SG'
  AND c.currency = 'SGD'
  AND c.locale   = 'en-SG'
  AND c.timezone = 'Asia/Singapore'
  AND o.created_at >= {{quarter_start}}
GROUP BY c.tier
ORDER BY c.tier;
