-- No schema change: the indexes each lazy load used are already there. The fix
-- is in the application, replacing lazy loading with one query (after.sql).
-- In an ORM this is eager loading: select_related / prefetch_related in
-- Django, includes/preload in Rails, JOIN FETCH in JPA.
SELECT 1;
