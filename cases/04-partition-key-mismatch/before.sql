-- Growth dashboard: most viewed pages over the last 3 days.
SELECT path, count(*) AS views, round(avg(duration_ms)) AS avg_ms
FROM page_views
WHERE viewed_at >= {{window_start}}
GROUP BY path
ORDER BY views DESC
LIMIT 20;
