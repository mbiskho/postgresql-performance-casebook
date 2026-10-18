-- Per-user activity feed: a user's 50 most recent page views.
SELECT viewed_at, path, duration_ms
FROM page_views
WHERE user_id = {{feed_user}}
ORDER BY viewed_at DESC
LIMIT 50;
