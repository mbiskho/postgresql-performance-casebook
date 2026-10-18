CREATE INDEX page_views_viewed_brin ON page_views USING brin (viewed_at);
ANALYZE page_views;
