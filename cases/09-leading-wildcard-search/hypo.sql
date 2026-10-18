-- HypoPG cannot simulate GIN indexes, but asking is cheap and the answer
-- (planner keeps the Seq Scan) is itself a lesson: a B-tree on full_name
-- does nothing for a leading wildcard.
SELECT * FROM hypopg_create_index('CREATE INDEX ON customers (full_name)');
