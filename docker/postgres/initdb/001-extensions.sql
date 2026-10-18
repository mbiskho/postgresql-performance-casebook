-- Extensions installed once, on a fresh data volume.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Read-only role used by postgres_exporter.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'exporter') THEN
    CREATE ROLE exporter LOGIN PASSWORD 'exporter-local-only';
  END IF;
END $$;
GRANT pg_monitor TO exporter;
