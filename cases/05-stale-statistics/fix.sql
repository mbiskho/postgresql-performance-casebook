-- Refresh the statistics so the planner sees how rare exceptions are now.
ANALYZE shipment_events;

-- And stop it recurring: turn autovacuum back on, and make autoanalyze fire
-- after 2% of the table changes instead of the default 10%, which on a
-- multi-million-row event table is a long time to plan on stale numbers.
ALTER TABLE shipment_events RESET (autovacuum_enabled);
ALTER TABLE shipment_events SET (autovacuum_analyze_scale_factor = 0.02);
