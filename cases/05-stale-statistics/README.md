# Stale statistics after a bulk load

**Symptom.** The ops dashboard shows the 50 most recent delivery exceptions.
The query looks trivial, both columns it uses are indexed, and it still reads
hundreds of megabytes per call.

**What the plan shows.** `Index Scan Backward using
shipment_events_created_at_idx` with `Filter: status = 'exception'`. PostgreSQL
starts from the newest event and walks backwards, checking each row, until
it has 50 exceptions. It expects to stop almost at once. The estimate on the
scan is about **2.07 million** matching rows, so it expects to find 50 within
the first ~120 rows it checks. In reality there are **941** exceptions in five
million events, most of them from go-live day. The scan reads ~2.4 million
rows before it finds 50.

**Why.** The statistics are from a different table. `shipment_events` was
analyzed on go-live day, when it held 2,000 rows and the carrier integration
was marking 40% of events as exceptions. The import job had turned autovacuum
off for the table, so autoanalyze never ran again while five million
healthy events were appended. The planner still believed 40% of rows are
exceptions. Given that belief, walking the time index and stopping early is
the right plan. The plan wasn't the mistake; the statistics were.

**Fix.** `ANALYZE shipment_events`. With current statistics the estimate drops
to a few hundred rows, and the planner switches to the `status` index: it
fetches the ~941 exceptions directly and sorts them. To stop it recurring,
autovacuum is switched back on for the table, and its analyze threshold is
lowered from the default 10% of rows to 2%. On an event table with tens of
millions of rows, 10% is a lot of change to plan around.

**Trade-offs.** Almost none. `ANALYZE` reads a fixed-size sample (30,000 rows
at the default statistics target) and takes milliseconds to seconds. A
more frequent autoanalyze costs a little I/O. An index on `(status,
created_at)` would make this one query fast whatever the statistics say, but
it hides the real problem, which will come back for the next query that
filters on `status`.

**Catching it in production.** Compare `last_analyze`/`last_autoanalyze` in
`pg_stat_user_tables` with `n_mod_since_analyze`. A large table with
millions of modifications since its last analyze is planning on a
description of the past. Check `reloptions` for `autovacuum_enabled=false`
left behind by import scripts. In plans, look for estimates that differ from
actuals by orders of magnitude, especially above a `LIMIT`.
