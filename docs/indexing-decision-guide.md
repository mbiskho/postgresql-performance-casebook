# Indexing and partitioning decision guide

These are the rules of thumb behind the ten cases, written as the questions to
ask in order. Each links to the case where the measurement backs it up. Read
the casebook (`reports/casebook.md`) for the numbers.

## 0. Find the statement before choosing a fix

- **Rank by total time, not mean time.** `pg_stat_statements` ordered by
  `total_exec_time` finds the statement costing the most. A 0.3 ms query
  called 180 times per page (case 03) costs more than a 2 s report run once a
  day.
- **Then look at blocks per call.** `shared_blks_read / calls` is the signal
  that a statement does far more I/O than its result needs (cases 01, 06).
- **Capture the real plan.** `auto_explain` (configured here at 500 ms with
  row counts and buffers) records the plan the application actually got,
  with its own parameters. Don't trust a plan you get by hand in psql: the
  parameters, the cache state or the statistics may all be different.

## 1. Read the plan for these four things

| Look for | It usually means | Case |
|---|---|---|
| `Rows Removed by Filter` far larger than rows returned | The access path finds rows it can't use: wrong or missing index, or a predicate the index can't serve | 01, 02, 05, 09 |
| Estimated rows off by 10x or more on a node that feeds a join or a LIMIT | A statistics problem; an index won't fix it | 05, 06 |
| `loops=` in the tens of thousands under a Nested Loop | The planner expected a handful of outer rows | 06 |
| A plan touching hundreds of MB to return a page of rows | The query asks for more work than the product needs | 08, 10 |

If none of these is present and the statement is still slow in production,
the problem is probably outside the plan: round trips (case 03) or lock
waits (case 07). Check `wait_event_type` in `pg_stat_activity` while it is
slow.

## 2. B-tree indexes

**Column order: equality, then sort, then range.** Put columns compared with
`=` (or a short `IN` list) first, then the `ORDER BY` column, then range
predicates. The index then returns rows already filtered and already in order,
and a `LIMIT` stops the scan early. `(customer_id, status, created_at DESC)`
in case 01 follows this rule.

**A composite index replaces its own prefix.** Once `(customer_id, status,
created_at)` exists, a separate `(customer_id)` index serves nothing the
composite can't, and every insert still has to maintain it. Drop it in the same
change; case 01 and case 08 both do.

**Partial indexes for rare, hot subsets.** If a query only ever asks for a
small slice of rows (open orders, unprocessed jobs, failed payments), index
just that slice: `... WHERE status IN ('pending', 'paid', 'shipped')`. The index
is a fraction of the size and stays hot in cache. The cost is that it serves
only queries that repeat the same predicate. Case 01 measures this against the
full composite.

**Fix the predicate before adding an expression index.** `created_at::date =
$1` can't use an index on `created_at`. Rewriting it as a half-open range
costs nothing; an expression index costs a second full index on the table
(case 02 measures both). Use an expression index when the application can't
change the query, or the expression is something like `lower(email)` where no
rewrite exists.

**Use keyset pagination instead of deep OFFSETs.** `OFFSET n` reads and
discards n rows. Paginate on an indexed, unique ordering such as `(created_at,
id)` and continue with `WHERE (created_at, id) < ($last_created_at, $last_id)`
(case 08). This also stops rows being skipped or shown twice when new rows
arrive between page loads.

**Check with HypoPG before you build.** `hypopg_create_index()` tells you, in
milliseconds and without writing anything, whether the planner would use an
index and roughly how large it would be. HypoPG only simulates B-tree-like
indexes. For GIN and BRIN, build the index on a copy instead.

## 3. Other index types

| Need | Index | Notes |
|---|---|---|
| Substring / `ILIKE '%x%'` search on short text | GIN with `gin_trgm_ops` (pg_trgm) | Large and slow to update: budget roughly the size of the column. Patterns shorter than 3 characters fall back to a full scan. Case 09. |
| Word search in documents | GIN on `to_tsvector(...)` | Use full-text search, not trigrams, when users search for words rather than fragments. |
| Range filters on a column that tracks insertion order (timestamps on append-only tables) | BRIN | Kilobytes instead of hundreds of MB. Useless if rows are updated or inserted out of order. Case 04's alternative. |
| Equality only, very long keys | Hash | Rarely worth it over B-tree since PG 10 made it crash-safe; B-tree also does ranges and ordering. |

## 4. Statistics before indexes

- **After a bulk load, run ANALYZE.** Autoanalyze fires after
  `autovacuum_analyze_scale_factor` (default 10%) of the table changes, and
  only if autovacuum is enabled for that table. Import jobs often turn it off
  for speed and never turn it back on (case 05). For large append-heavy
  tables, lower the scale factor per table instead of globally.
- **Columns that move together need extended statistics.** The planner
  multiplies selectivities as if columns were independent. `country`,
  `currency`, `locale` and `timezone` are not independent, so four filters
  that select 2% of customers were estimated at 0.00000016% (case 06).
  `CREATE STATISTICS ... (dependencies, mcv)` fixes it. `ndistinct` does the
  same for `GROUP BY` estimates.
- **Skewed columns may need a larger sample.** `ALTER TABLE ... ALTER COLUMN
  ... SET STATISTICS 1000` keeps more most-common values for columns with a
  long tail of popular values.
- **Some correlations can't be expressed.** Extended statistics cover
  equality and the most-common-value list. They don't help "status is pending
  *and* the order is older than 30 days", where the correlation is between a
  value and a range. For that, use a partial index or rewrite the query.

## 5. Partitioning

**Partition by what queries and retention filter on.** Pruning only helps
queries that constrain the partition key. Most event, log and order data is
read by time and deleted by time, so the key is usually time. Case 04's table
was hash-partitioned by user for one feed query, and as a result every
time-based query and the retention job had to touch all 16 partitions.

**Retention is the strongest argument for partitioning.** Dropping a monthly
partition is a catalog change that takes milliseconds. A `DELETE` of the same
rows writes WAL for every row, leaves dead tuples for vacuum, and took 1.5 s
on this machine for five months of a small table (case 04).

**Partitioning is not an index.** For a single slow time-range query on an
append-ordered table, a BRIN index may be the whole answer, and it was the
fastest option measured in case 04. Repartition when retention, vacuum or
bulk-load patterns justify it, not to speed up one query.

**Keep partition counts modest.** Planning cost grows with the number of
partitions a query can't prune. Monthly partitions over a few years (tens) are
comfortable. Daily partitions over years (thousands) need every query to prune
well.

**Predicates must use the raw key.** `WHERE viewed_at::date = ...` defeats
pruning for the same reason it defeats an index (case 02).

**Constraints and indexes are per partition.** A unique constraint must
include the partition key. Indexes created on the parent are created on each
partition, which is what keeps the per-user feed working in case 04 after the
move to time partitions.

## 6. When the database is the wrong place to fix it

- **Round trips:** if a page issues one query per row (case 03), fix the
  data-access code. The database is already doing each query in microseconds.
- **Transaction scope:** if a transaction holds a lock while it waits on
  something outside the database (case 07), make the transaction shorter.
  More hardware won't remove a lock queue.
- **The requirement:** if a dashboard recomputes an exact count over all
  history on every page view (case 10), ask whether it needs to. A rollup
  maintained by a job, or a number cached for a minute, is a product decision
  that's cheaper than any index.
