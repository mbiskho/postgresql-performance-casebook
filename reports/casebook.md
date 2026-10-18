# PostgreSQL Performance Casebook

Ten slow queries on a 64-million-row dataset, each with the plan PostgreSQL chose, what it was getting wrong, the fix, and the measured difference. Everything in this document is generated from `results/` by `make report`; rerun `make run` to reproduce it on your machine.

**10 of 10 cases meet the 5x target. Median improvement 63.5x.**

## How the numbers were measured

| | |
|---|---|
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg12+2) |
| Dataset | scale 1.0: 64,413,408 rows, 6928 MB on disk |
| Rows | customers 2,000,000, products 100,000, orders 15,000,000, order_items 37,313,408, page_views 10,000,000 |
| Machine | Docker VM, 8 vCPUs, 3.8 GB RAM, SSD |
| Key settings | shared_buffers 1310728kB, work_mem 16384kB, random_page_cost 1.1, parallel workers/gather 2, jit off |

- Each statement runs warm-up passes, then a fixed number of timed passes inside `BEGIN ... ROLLBACK`. The headline is the mean server execution time from `pg_stat_statements` over the timed passes, so it excludes network and driver overhead. Client wall clock is reported next to it.
- Buffers, rows removed and row-estimate errors come from one `EXPLAIN (ANALYZE, BUFFERS)` after the timed passes. The q-error is max(estimate/actual, actual/estimate) for the worst node, ignoring nodes under a LIMIT that stopped early.
- `pg_stat_statements` has no CPU counter. "Not waiting on I/O" is execution time minus `track_io_timing` read/write time. It stands in for CPU on a serial plan and understates it on a parallel one.
- The dataset (~7 GB) is larger than the VM's memory, so caches are warm but not complete. Large scans still read from the OS page cache or disk, as they would on a real instance. Runs on other hardware will differ in absolute terms; the plan shapes and ratios are the point.
- Each fix's result set is fingerprinted and compared with the slow version's. A fix that changes the answer fails the case.

## Summary

| # | Case | Category | Before | After | Improvement | Index / table cost |
|---|---|---|---:|---:|---:|---:|
| 01 | [Missing composite index](#01-missing-composite-index) | Indexing | 13.04 ms | 0.15 ms | 87.0x | +564.0 MB |
| 02 | [Non-sargable date predicate](#02-non-sargable-date-predicate) | Query shape | 1,413.98 ms | 11.86 ms | 119x | none |
| 03 | [N+1 queries from an ORM loop](#03-n1-queries-from-an-orm-loop) | Application access pattern | 27.70 ms | 1.48 ms | 18.7x | none |
| 04 | [Partition key does not match the access path](#04-partition-key-does-not-match-the-access-path) | Partitioning | 974.13 ms | 24.34 ms | 40.0x | +1,004.8 MB |
| 05 | [Stale statistics after a bulk load](#05-stale-statistics-after-a-bulk-load) | Statistics | 188.49 ms | 0.36 ms | 525x | none |
| 06 | [Correlated columns fool the row estimate](#06-correlated-columns-fool-the-row-estimate) | Statistics | 11,197.75 ms | 1,004.23 ms | 11.2x | none |
| 07 | [Hot-row lock contention](#07-hot-row-lock-contention) | Concurrency | 231 TPS | 7,942 TPS | 34.4x | none |
| 08 | [Deep OFFSET pagination](#08-deep-offset-pagination) | Query shape | 32.52 ms | 0.03 ms | 1,301x | +129.3 MB |
| 09 | [Leading-wildcard search](#09-leading-wildcard-search) | Indexing | 366.51 ms | 12.23 ms | 30.0x | +46.1 MB |
| 10 | [Exact counts on every page load](#10-exact-counts-on-every-page-load) | Don't fix it in the database | 1,599.31 ms | 4.70 ms | 341x | +344.0 KB |

Indexing and partitioning decisions drawn from these cases: [docs/indexing-decision-guide.md](../docs/indexing-decision-guide.md).

## 01. Missing composite index

_An "open orders" panel filters on customer and status and sorts by date, but only a single-column customer_id index exists._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 13.0 ms | 0.15 ms |
| Client wall clock (median of 9 / 9) | 13.5 ms | 0.52 ms |
| Waiting on block I/O | 0.00 ms | 0.00 ms |
| Not waiting on I/O (CPU proxy) | 13.0 ms | 0.15 ms |
| Buffers touched (hit + read) | 145.15 MB | 3.2 MB |
| Blocks read from outside shared_buffers | 0 | 0 |
| Rows read then discarded by filters | 19,366 | 0 |
| Worst row-estimate error (q-error) | 1.0x | 1.0x |
| Rows returned | 20 | 20 |
| Plan nodes | Bitmap Heap Scan, Bitmap Index Scan, Limit, Sort | Index Scan, Limit, Sort |

**Result: 13.04 ms → 0.15 ms (87.0x on server execution time); result set n/a; meets the 5x target.**

Storage cost of the fix:

| Relation | Kind | Change | Size after |
|---|---|---:|---:|
| `orders_customer_id_idx` | index (dropped) | -144.2 MB | 0 B |
| `orders_customer_status_created_idx` | index (added) | +708.2 MB | +708.2 MB |

Net index change +564.0 MB, net table change 0 B. The fix took 18.7 s to apply.

HypoPG what-if before building anything: estimated plan cost 17,640 → 380; planner would use the hypothetical index <13561>btree_orders_customer_id_status_created_at (~768 MB).

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- "Open orders" panel on the account page: the 20 most recent orders that
-- are still in flight.
SELECT id, status, total_cents, created_at
FROM orders
WHERE customer_id = 1
  AND status IN ('pending', 'paid', 'shipped')
ORDER BY created_at DESC
LIMIT 20;
```

After:

```sql
-- "Open orders" panel on the account page: the 20 most recent orders that
-- are still in flight.
SELECT id, status, total_cents, created_at
FROM orders
WHERE customer_id = 1
  AND status IN ('pending', 'paid', 'shipped')
ORDER BY created_at DESC
LIMIT 20;
```

Fix:

```sql
-- Equality columns first, then the sort column, so the index hands back rows
-- already in the order the page wants and the scan stops after 20.
CREATE INDEX orders_customer_status_created_idx
  ON orders (customer_id, status, created_at DESC);

-- (customer_id) is now a prefix of the new index. Any query that used it can
-- use the composite instead, so keeping it only costs write amplification and
-- disk.
DROP INDEX orders_customer_id_idx;
```

Plan before:

```
Limit  (cost=17639.74..17639.79 rows=20 width=33) (actual time=13.170..13.173 rows=20 loops=1)
  Buffers: shared hit=18579
  ->  Sort  (cost=17639.74..17640.56 rows=328 width=33) (actual time=13.170..13.171 rows=20 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 27kB
        Buffers: shared hit=18579
        ->  Bitmap Heap Scan on orders  (cost=151.12..17631.01 rows=328 width=33) (actual time=12.852..13.110 rows=402 loops=1)
              Recheck Cond: (customer_id = 1)
              Filter: (status = ANY ('{pending,paid,shipped}'::text[]))
              Rows Removed by Filter: 19366
              Heap Blocks: exact=18560
              Buffers: shared hit=18579
              ->  Bitmap Index Scan on orders_customer_id_idx  (cost=0.00..151.03 rows=17000 width=0) (actual time=1.275..1.276 rows=19768 loops=1)
                    Index Cond: (customer_id = 1)
                    Buffers: shared hit=19
Planning Time: 0.049 ms
Execution Time: 13.272 ms
```

Plan after:

```
Limit  (cost=381.04..381.09 rows=20 width=33) (actual time=0.174..0.176 rows=20 loops=1)
  Buffers: shared hit=410
  ->  Sort  (cost=381.04..381.86 rows=328 width=33) (actual time=0.174..0.175 rows=20 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 26kB
        Buffers: shared hit=410
        ->  Index Scan using orders_customer_status_created_idx on orders  (cost=0.56..372.31 rows=328 width=33) (actual time=0.008..0.129 rows=402 loops=1)
              Index Cond: ((customer_id = 1) AND (status = ANY ('{pending,paid,shipped}'::text[])))
              Buffers: shared hit=410
Planning Time: 0.029 ms
Execution Time: 0.183 ms
```

Full plans (JSON and text): `results/01-missing-composite-index/`.

</details>

## 02. Non-sargable date predicate

_A daily export casts created_at to a date, so the existing created_at index cannot be used and every run scans 15M rows._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 1.41 s | 11.9 ms |
| Client wall clock (median of 7 / 7) | 1.68 s | 252.6 ms |
| Waiting on block I/O | 1.74 s | 0.00 ms |
| Not waiting on I/O (CPU proxy) | -326.75 ms | 11.9 ms |
| Buffers touched (hit + read) | 1,206.52 MB | 2.31 MB |
| Blocks read from outside shared_buffers | 131,651 | 0 |
| Rows read then discarded by filters | 14,977,620 | 0 |
| Worst row-estimate error (q-error) | 4.2x | 1.0x |
| Rows returned | 22,381 | 22,381 |
| Plan nodes | Gather Merge, Seq Scan, Sort | Index Scan, Sort |

**Result: 1,413.98 ms → 11.86 ms (119x on server execution time); result set n/a; meets the 5x target.**

The fix adds no storage. It took 0.0 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Finance export: every order placed on a given (UTC) calendar day.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE (created_at AT TIME ZONE 'UTC')::date = '2026-03-14'::date
ORDER BY id;
```

After:

```sql
-- Same day, written as a half-open range on the raw column. The index on
-- created_at can now bound the scan.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE created_at >= '2026-03-14'::date::timestamp AT TIME ZONE 'UTC'
  AND created_at <  ('2026-03-14'::date + 1)::timestamp AT TIME ZONE 'UTC'
ORDER BY id;
```

Fix:

```sql
-- No schema change. The fix is the rewrite in after.sql: the predicate now
-- compares the indexed column directly, so orders_created_at_idx applies.
--
-- An expression index on ((created_at AT TIME ZONE 'UTC')::date) would also
-- work, but it costs ~300 MB and slows every insert to fix one query that
-- can be rewritten for free.
SELECT 1;
```

Plan before:

```
Gather Merge  (cost=267029.08..274321.26 rows=62500 width=41) (actual time=1429.071..1439.322 rows=22381 loops=1)
  Workers Planned: 2
  Workers Launched: 2
  Buffers: shared hit=22880 read=131555
  I/O Timings: shared read=1785.464
  ->  Sort  (cost=266029.06..266107.18 rows=31250 width=41) (actual time=1424.631..1424.978 rows=7460 loops=3)
        Sort Key: id
        Sort Method: quicksort  Memory: 1255kB
        Buffers: shared hit=22880 read=131555
        I/O Timings: shared read=1785.464
        Worker 0:  Sort Method: quicksort  Memory: 1065kB
        Worker 1:  Sort Method: quicksort  Memory: 42kB
        ->  Parallel Seq Scan on orders  (cost=0.00..263696.00 rows=31250 width=41) (actual time=981.027..1420.943 rows=7460 loops=3)
              Filter: (((created_at AT TIME ZONE 'UTC'::text))::date = '2026-03-14'::date)
              Rows Removed by Filter: 4992540
              Buffers: shared hit=22766 read=131555
              I/O Timings: shared read=1785.464
Planning Time: 0.267 ms
Execution Time: 1440.668 ms
```

Plan after:

```
Sort  (cost=2366.47..2422.47 rows=22402 width=41) (actual time=4.399..5.066 rows=22381 loops=1)
  Sort Key: id
  Sort Method: quicksort  Memory: 2337kB
  Buffers: shared hit=296
  ->  Index Scan using orders_created_at_idx on orders  (cost=0.43..747.77 rows=22402 width=41) (actual time=0.006..2.215 rows=22381 loops=1)
        Index Cond: ((created_at >= '2026-03-14 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-03-15 00:00:00+00'::timestamp with time zone))
        Buffers: shared hit=296
Planning Time: 0.028 ms
Execution Time: 5.736 ms
```

Full plans (JSON and text): `results/02-non-sargable-predicate/`.

</details>

## 03. N+1 queries from an ORM loop

_An order-history page loads 50 orders, then each order's items, then each item's product, one query at a time: ~180 round trips for one page. Every individual query is fast, which is why nobody notices._

### Measured

| | Before (lazy loading) | After (one query) |
|---|---:|---:|
| Page render time, client (median of 15 / 15) | 27.7 ms | 1.48 ms |
| Round trips per page | 179 | 1 |
| Server execution time per page (sum of statements) | 0.86 ms | 0.52 ms |
| Statements executed per page | 179 | 1 |
| Buffers touched per page | 6.2 MB | 6.17 MB |

Every individual query in the N+1 version is an index lookup that runs in well under a millisecond on the server; the page is slow because of the number of round trips, which is why the headline metric for this case is client-side page time.

**Result: 27.70 ms → 1.48 ms (18.7x on page render time (client)); result set n/a; meets the 5x target.**

The fix adds no storage. It took 0.0 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql

```

After:

```sql
-- One round trip: the same page as a single statement. Items and products
-- are joined and folded into one JSON array per order, which is the shape the
-- page template wanted all along.
SELECT o.id AS order_id, o.status, o.total_cents, o.created_at,
       json_agg(json_build_object(
           'item_id', i.id, 'product_id', i.product_id, 'name', p.name, 'brand', p.brand,
           'quantity', i.quantity, 'unit_price_cents', i.unit_price_cents)
         ORDER BY i.id) AS items
FROM (
    SELECT id, status, total_cents, created_at
    FROM orders
    WHERE customer_id = 1021
    ORDER BY created_at DESC
    LIMIT 50
) o
JOIN order_items i ON i.order_id = o.id
JOIN products p    ON p.id = i.product_id
GROUP BY o.id, o.status, o.total_cents, o.created_at
ORDER BY o.created_at DESC;
```

Fix:

```sql
-- No schema change: the indexes each lazy load used are already there. The fix
-- is in the application, replacing lazy loading with one query (after.sql).
-- In an ORM this is eager loading: select_related / prefetch_related in
-- Django, includes/preload in Rails, JOIN FETCH in JPA.
SELECT 1;
```

Plan before:

```
-- parent query (executed once)
Limit  (cost=32.35..32.41 rows=27 width=33) (actual time=0.087..0.091 rows=50 loops=1)
  Buffers: shared hit=203
  ->  Sort  (cost=32.35..32.41 rows=27 width=33) (actual time=0.086..0.088 rows=50 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 32kB
        Buffers: shared hit=203
        ->  Index Scan using orders_customer_id_idx on orders  (cost=0.43..31.70 rows=27 width=33) (actual time=0.004..0.053 rows=200 loops=1)
              Index Cond: (customer_id = 1021)
              Buffers: shared hit=203
Planning Time: 0.020 ms
Execution Time: 0.097 ms


-- child query (executed per row)
Sort  (cost=4.44..4.51 rows=27 width=24) (actual time=0.005..0.005 rows=4 loops=1)
  Sort Key: id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=4
  ->  Index Scan using order_items_order_id_idx on order_items  (cost=0.44..3.80 rows=27 width=24) (actual time=0.003..0.003 rows=4 loops=1)
        Index Cond: (order_id = 14956450)
        Buffers: shared hit=4
Planning Time: 0.014 ms
Execution Time: 0.009 ms


-- grandchild query (executed per row)
Index Scan using products_pkey on products  (cost=0.29..2.51 rows=1 width=39) (actual time=0.003..0.003 rows=1 loops=1)
  Index Cond: (id = 58143)
  Buffers: shared hit=3
Planning Time: 0.011 ms
Execution Time: 0.008 ms
```

Plan after:

```
GroupAggregate  (cost=46.11..405.77 rows=27 width=65) (actual time=0.163..0.514 rows=50 loops=1)
  Group Key: orders.created_at, orders.id, orders.status, orders.total_cents
  Buffers: shared hit=790
  ->  Incremental Sort  (cost=46.11..394.53 rows=727 width=88) (actual time=0.155..0.316 rows=128 loops=1)
        Sort Key: orders.created_at DESC, orders.id, orders.status, orders.total_cents, i.id
        Presorted Key: orders.created_at
        Full-sort Groups: 4  Sort Method: quicksort  Average Memory: 29kB  Peak Memory: 29kB
        Buffers: shared hit=790
        ->  Nested Loop  (cost=33.08..367.63 rows=727 width=88) (actual time=0.095..0.286 rows=128 loops=1)
              Buffers: shared hit=790
              ->  Nested Loop  (cost=32.79..142.22 rows=727 width=57) (actual time=0.093..0.174 rows=128 loops=1)
                    Buffers: shared hit=406
                    ->  Limit  (cost=32.35..32.41 rows=27 width=33) (actual time=0.091..0.096 rows=50 loops=1)
                          Buffers: shared hit=203
                          ->  Sort  (cost=32.35..32.41 rows=27 width=33) (actual time=0.091..0.093 rows=50 loops=1)
                                Sort Key: orders.created_at DESC
                                Sort Method: top-N heapsort  Memory: 32kB
                                Buffers: shared hit=203
                                ->  Index Scan using orders_customer_id_idx on orders  (cost=0.43..31.70 rows=27 width=33) (actual time=0.008..0.053 rows=200 loops=1)
                                      Index Cond: (customer_id = 1021)
                                      Buffers: shared hit=203
                    ->  Index Scan using order_items_order_id_idx on order_items i  (cost=0.44..3.80 rows=27 width=32) (actual time=0.001..0.001 rows=3 loops=50)
                          Index Cond: (order_id = orders.id)
                          Buffers: shared hit=203
              ->  Index Scan using products_pkey on products p  (cost=0.29..0.31 rows=1 width=39) (actual time=0.001..0.001 rows=1 loops=128)
                    Index Cond: (id = i.product_id)
                    Buffers: shared hit=384
Planning:
  Buffers: shared hit=14
Planning Time: 0.089 ms
Execution Time: 0.527 ms
```

Full plans (JSON and text): `results/03-n-plus-one/`.

</details>

## 04. Partition key does not match the access path

_page_views is hash-partitioned by user_id for a per-user feed, but every dashboard and the retention job filter on time. No query can prune, so a 3-day report scans all 16 partitions._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 974.1 ms | 24.3 ms |
| Client wall clock (median of 7 / 7) | 982.4 ms | 24.8 ms |
| Waiting on block I/O | 2.82 ms | 0.00 ms |
| Not waiting on I/O (CPU proxy) | 971.3 ms | 24.3 ms |
| Buffers touched (hit + read) | 704.05 MB | 76.16 MB |
| Blocks read from outside shared_buffers | 0 | 0 |
| Rows read then discarded by filters | 9,889,199 | 969,444 |
| Worst row-estimate error (q-error) | 1.0x | 1.0x |
| Rows returned | 20 | 20 |
| Plan nodes | Aggregate, Append, Gather Merge, Limit, Seq Scan, Sort | Aggregate, Append, Gather Merge, Limit, Seq Scan, Sort |

**Result: 974.13 ms → 24.34 ms (40.0x on server execution time); result set identical; meets the 5x target.**

Storage cost of the fix:

| Relation | Kind | Change | Size after |
|---|---|---:|---:|
| `page_views_m2025_04` | table (added) | +1.2 MB | +1.2 MB |
| `page_views_m2025_04_user_id_viewed_at_idx` | index (added) | +528.0 KB | +528.0 KB |
| `page_views_m2025_05` | table (added) | +5.6 MB | +5.6 MB |
| `page_views_m2025_05_user_id_viewed_at_idx` | index (added) | +2.4 MB | +2.4 MB |
| `page_views_m2025_06` | table (added) | +9.9 MB | +9.9 MB |
| `page_views_m2025_06_user_id_viewed_at_idx` | index (added) | +4.2 MB | +4.2 MB |
| `page_views_m2025_07` | table (added) | +14.7 MB | +14.7 MB |
| `page_views_m2025_07_user_id_viewed_at_idx` | index (added) | +6.3 MB | +6.3 MB |
| `page_views_m2025_08` | table (added) | +19.4 MB | +19.4 MB |
| `page_views_m2025_08_user_id_viewed_at_idx` | index (added) | +8.3 MB | +8.3 MB |
| `page_views_m2025_09` | table (added) | +23.2 MB | +23.2 MB |
| `page_views_m2025_09_user_id_viewed_at_idx` | index (added) | +9.9 MB | +9.9 MB |
| `page_views_m2025_10` | table (added) | +28.5 MB | +28.5 MB |
| `page_views_m2025_10_user_id_viewed_at_idx` | index (added) | +12.2 MB | +12.2 MB |
| `page_views_m2025_11` | table (added) | +32.0 MB | +32.0 MB |
| `page_views_m2025_11_user_id_viewed_at_idx` | index (added) | +13.7 MB | +13.7 MB |
| `page_views_m2025_12` | table (added) | +37.6 MB | +37.6 MB |
| `page_views_m2025_12_user_id_viewed_at_idx` | index (added) | +16.1 MB | +16.1 MB |
| `page_views_m2026_01` | table (added) | +42.3 MB | +42.3 MB |
| `page_views_m2026_01_user_id_viewed_at_idx` | index (added) | +18.1 MB | +18.1 MB |
| `page_views_m2026_02` | table (added) | +42.2 MB | +42.2 MB |
| `page_views_m2026_02_user_id_viewed_at_idx` | index (added) | +18.0 MB | +18.0 MB |
| `page_views_m2026_03` | table (added) | +51.1 MB | +51.1 MB |
| `page_views_m2026_03_user_id_viewed_at_idx` | index (added) | +21.8 MB | +21.8 MB |
| `page_views_m2026_04` | table (added) | +53.9 MB | +53.9 MB |
| `page_views_m2026_04_user_id_viewed_at_idx` | index (added) | +23.0 MB | +23.0 MB |
| `page_views_m2026_05` | table (added) | +60.2 MB | +60.2 MB |
| `page_views_m2026_05_user_id_viewed_at_idx` | index (added) | +25.7 MB | +25.7 MB |
| `page_views_m2026_06` | table (added) | +62.7 MB | +62.7 MB |
| `page_views_m2026_06_user_id_viewed_at_idx` | index (added) | +26.8 MB | +26.8 MB |
| `page_views_m2026_07` | table (added) | +69.4 MB | +69.4 MB |
| `page_views_m2026_07_user_id_viewed_at_idx` | index (added) | +29.6 MB | +29.6 MB |
| `page_views_m2026_08` | table (added) | +74.0 MB | +74.0 MB |
| `page_views_m2026_08_user_id_viewed_at_idx` | index (added) | +31.6 MB | +31.6 MB |
| `page_views_m2026_09` | table (added) | +76.0 MB | +76.0 MB |
| `page_views_m2026_09_user_id_viewed_at_idx` | index (added) | +32.5 MB | +32.5 MB |
| `page_views_m2026_10_user_id_viewed_at_idx` | index (added) | +8.0 KB | +8.0 KB |

Net index change +300.9 MB, net table change +703.9 MB. The fix took 34.4 s to apply.

Alternatives measured and not chosen:

| Alternative | Server time | Index/table cost | Why not |
|---|---:|---:|---|
| Keep hash partitioning, add a BRIN index on viewed_at | 18.4 ms | +384.0 KB | Each hash partition is still append-ordered by time, which is exactly what BRIN needs. Fixes the dashboard for a few hundred kilobytes of index; does nothing for retention. |

Other workloads affected by the fix:

| Workload | Before | After |
|---|---:|---:|
| Per-user activity feed (the query the hash scheme was built for) | 1.02 ms | 0.81 ms |
| Retention: remove data older than 13 months | 1.71 s | 0.73 ms |

*Repartitioning by time is not free: this query used to prune to one hash partition and now has to visit monthly partitions in order.*
*Under hash partitioning retention is a DELETE across every partition. Under time partitioning it is a DROP of whole partitions. Timed inside a rolled-back transaction.*

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Growth dashboard: most viewed pages over the last 3 days.
SELECT path, count(*) AS views, round(avg(duration_ms)) AS avg_ms
FROM page_views
WHERE viewed_at >= '2026-09-27 23:59:59+00:00'::timestamptz
GROUP BY path
ORDER BY views DESC
LIMIT 20;
```

After:

```sql
-- Growth dashboard: most viewed pages over the last 3 days.
SELECT path, count(*) AS views, round(avg(duration_ms)) AS avg_ms
FROM page_views
WHERE viewed_at >= '2026-09-27 23:59:59+00:00'::timestamptz
GROUP BY path
ORDER BY views DESC
LIMIT 20;
```

Fix:

```sql
-- Re-partition by time: one partition per calendar month (UTC).
CREATE TABLE page_views_by_month (
    user_id     bigint      NOT NULL,
    viewed_at   timestamptz NOT NULL,
    path        text        NOT NULL,
    referrer    text,
    duration_ms integer     NOT NULL
) PARTITION BY RANGE (viewed_at);

DO $$
DECLARE
  m date := date '2025-04-01';
BEGIN
  WHILE m < date '2026-11-01' LOOP
    EXECUTE format(
      'CREATE TABLE page_views_m%s PARTITION OF page_views_by_month
         FOR VALUES FROM (%L) TO (%L)',
      to_char(m, 'YYYY_MM'), m::timestamp AT TIME ZONE 'UTC',
      (m + interval '1 month')::timestamp AT TIME ZONE 'UTC');
    m := m + interval '1 month';
  END LOOP;
END $$;

-- Copy in time order, so each month's heap is append-ordered by viewed_at.
INSERT INTO page_views_by_month
SELECT * FROM page_views ORDER BY viewed_at;

-- Keep the per-user feed working: a local (user_id, viewed_at) index on each
-- month. Built after the load, which is much faster than maintaining it row by
-- row during the copy.
CREATE INDEX page_views_by_month_user_viewed_idx ON page_views_by_month (user_id, viewed_at);
VACUUM (ANALYZE) page_views_by_month;

-- Swap. In production this is the last step of an online migration
-- (dual-write or logical replication into the new table, then a short lock
-- for the rename); here the data is static, so a copy is enough.
ALTER TABLE page_views RENAME TO page_views_by_user_hash;
ALTER TABLE page_views_by_month RENAME TO page_views;
```

Plan before:

```
Limit  (cost=143869.20..143869.25 rows=20 width=46) (actual time=1090.354..1108.159 rows=20 loops=1)
  Buffers: shared hit=90118
  ->  Sort  (cost=143869.20..143869.95 rows=302 width=46) (actual time=1090.346..1108.149 rows=20 loops=1)
        Sort Key: (count(*)) DESC
        Sort Method: top-N heapsort  Memory: 26kB
        Buffers: shared hit=90118
        ->  Finalize GroupAggregate  (cost=143781.63..143861.16 rows=302 width=46) (actual time=1089.516..1108.042 rows=302 loops=1)
              Group Key: page_views.path
              Buffers: shared hit=90118
              ->  Gather Merge  (cost=143781.63..143852.10 rows=604 width=46) (actual time=1089.399..1107.528 rows=906 loops=1)
                    Workers Planned: 2
                    Workers Launched: 2
                    Buffers: shared hit=90118
                    ->  Sort  (cost=142781.61..142782.36 rows=302 width=46) (actual time=1082.581..1082.648 rows=302 loops=3)
                          Sort Key: page_views.path
                          Sort Method: quicksort  Memory: 48kB
                          Buffers: shared hit=90118
                          Worker 0:  Sort Method: quicksort  Memory: 48kB
                          Worker 1:  Sort Method: quicksort  Memory: 48kB
                          ->  Partial HashAggregate  (cost=142766.15..142769.17 rows=302 width=46) (actual time=1081.573..1081.660 rows=302 loops=3)
                                Group Key: page_views.path
                                Batches: 1  Memory Usage: 93kB
                                Buffers: shared hit=90102
                                Worker 0:  Batches: 1  Memory Usage: 93kB
                                Worker 1:  Batches: 1  Memory Usage: 93kB
                                ->  Parallel Append  (cost=0.00..142417.66 rows=46465 width=10) (actual time=214.503..1069.062 rows=36934 loops=3)
                                      Buffers: shared hit=90102
                                      ->  Parallel Seq Scan on page_views_h08 page_views_9  (cost=0.00..9228.10 rows=2904 width=10) (actual time=225.564..243.175 rows=7165 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
                                            Rows Removed by Filter: 641814
                                            Buffers: shared hit=5848
                                      ->  Parallel Seq Scan on page_views_h13 page_views_14  (cost=0.00..9173.99 rows=3124 width=10) (actual time=224.106..229.324 rows=7208 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
                                            Rows Removed by Filter: 638103
                                            Buffers: shared hit=5813
                                      ->  Parallel Seq Scan on page_views_h07 page_views_8  (cost=0.00..8955.72 rows=2927 width=10) (actual time=294.414..297.062 rows=6934 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
                                            Rows Removed by Filter: 622772
                                            Buffers: shared hit=5676
                                      ->  Parallel Seq Scan on page_views_h12 page_views_13  (cost=0.00..8924.51 rows=2760 width=10) (actual time=315.340..318.336 rows=6819 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
                                            Rows Removed by Filter: 620926
                                            Buffers: shared hit=5655
                                      ->  Parallel Seq Scan on page_views_h01 page_views_2  (cost=0.00..8923.77 rows=2890 width=10) (actual time=206.036..210.875 rows=7088 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
... (48 more lines in the plan file)
```

Plan after:

```
Limit  (cost=17025.96..17026.01 rows=20 width=46) (actual time=25.250..26.819 rows=20 loops=1)
  Buffers: shared hit=9749
  ->  Sort  (cost=17025.96..17026.71 rows=302 width=46) (actual time=25.249..26.817 rows=20 loops=1)
        Sort Key: (count(*)) DESC
        Sort Method: top-N heapsort  Memory: 26kB
        Buffers: shared hit=9749
        ->  Finalize GroupAggregate  (cost=16938.39..17017.92 rows=302 width=46) (actual time=24.896..26.785 rows=302 loops=1)
              Group Key: page_views.path
              Buffers: shared hit=9749
              ->  Gather Merge  (cost=16938.39..17008.86 rows=604 width=46) (actual time=24.890..26.598 rows=906 loops=1)
                    Workers Planned: 2
                    Workers Launched: 2
                    Buffers: shared hit=9749
                    ->  Sort  (cost=15938.37..15939.12 rows=302 width=46) (actual time=23.555..23.566 rows=302 loops=3)
                          Sort Key: page_views.path
                          Sort Method: quicksort  Memory: 48kB
                          Buffers: shared hit=9749
                          Worker 0:  Sort Method: quicksort  Memory: 48kB
                          Worker 1:  Sort Method: quicksort  Memory: 48kB
                          ->  Partial HashAggregate  (cost=15922.91..15925.93 rows=302 width=46) (actual time=23.286..23.312 rows=302 loops=3)
                                Group Key: page_views.path
                                Batches: 1  Memory Usage: 93kB
                                Buffers: shared hit=9733
                                Worker 0:  Batches: 1  Memory Usage: 93kB
                                Worker 1:  Batches: 1  Memory Usage: 93kB
                                ->  Parallel Append  (cost=0.00..15584.73 rows=45090 width=10) (actual time=13.639..19.001 rows=36934 loops=3)
                                      Buffers: shared hit=9733
                                      ->  Parallel Seq Scan on page_views_m2026_09 page_views_1  (cost=0.00..15359.28 rows=45089 width=10) (actual time=13.638..17.017 rows=36934 loops=3)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
                                            Rows Removed by Filter: 323148
                                            Buffers: shared hit=9733
                                      ->  Parallel Seq Scan on page_views_m2026_10 page_views_2  (cost=0.00..0.00 rows=1 width=36) (actual time=0.001..0.001 rows=0 loops=1)
                                            Filter: (viewed_at >= '2026-09-27 23:59:59+00'::timestamp with time zone)
Planning:
  Buffers: shared hit=1
Planning Time: 0.086 ms
Execution Time: 26.843 ms
```

Full plans (JSON and text): `results/04-partition-key-mismatch/`.

</details>

## 05. Stale statistics after a bulk load

_Statistics were gathered on the first day of carrier events, when 40% were exceptions. Months of healthy events later, the planner still thinks exceptions are common and walks the whole created_at index looking for them._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 188.5 ms | 0.36 ms |
| Client wall clock (median of 7 / 7) | 189.6 ms | 1.40 ms |
| Waiting on block I/O | 0.00 ms | 0.00 ms |
| Not waiting on I/O (CPU proxy) | 188.5 ms | 0.36 ms |
| Buffers touched (hit + read) | 276.55 MB | 0.94 MB |
| Blocks read from outside shared_buffers | 0 | 0 |
| Rows read then discarded by filters | 2,386,328 | 0 |
| Worst row-estimate error (q-error) | 1.0x | 1.0x |
| Rows returned | 50 | 50 |
| Plan nodes | Index Scan, Limit | Index Scan, Limit, Sort |

**Result: 188.49 ms → 0.36 ms (525x on server execution time); result set identical; meets the 5x target.**

The fix adds no storage. It took 0.4 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Ops dashboard: the 50 most recent delivery exceptions.
SELECT id, order_id, carrier, created_at
FROM shipment_events
WHERE status = 'exception'
ORDER BY created_at DESC
LIMIT 50;
```

After:

```sql
-- Ops dashboard: the 50 most recent delivery exceptions.
SELECT id, order_id, carrier, created_at
FROM shipment_events
WHERE status = 'exception'
ORDER BY created_at DESC
LIMIT 50;
```

Fix:

```sql
-- Refresh the statistics so the planner sees how rare exceptions are now.
ANALYZE shipment_events;

-- And stop it recurring: turn autovacuum back on, and make autoanalyze fire
-- after 2% of the table changes instead of the default 10%, which on a
-- multi-million-row event table is a long time to plan on stale numbers.
ALTER TABLE shipment_events RESET (autovacuum_enabled);
ALTER TABLE shipment_events SET (autovacuum_analyze_scale_factor = 0.02);
```

Plan before:

```
Limit  (cost=0.43..3.94 rows=50 width=29) (actual time=2.414..185.570 rows=50 loops=1)
  Buffers: shared hit=35399
  ->  Index Scan Backward using shipment_events_created_at_idx on shipment_events  (cost=0.43..145398.56 rows=2070197 width=29) (actual time=2.413..185.563 rows=50 loops=1)
        Filter: (status = 'exception'::text)
        Rows Removed by Filter: 2386328
        Buffers: shared hit=35399
Planning Time: 0.048 ms
Execution Time: 185.591 ms
```

Plan after:

```
Limit  (cost=360.20..360.33 rows=50 width=29) (actual time=0.290..0.295 rows=50 loops=1)
  Buffers: shared hit=120
  ->  Sort  (cost=360.20..361.03 rows=333 width=29) (actual time=0.290..0.292 rows=50 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 32kB
        Buffers: shared hit=120
        ->  Index Scan using shipment_events_status_idx on shipment_events  (cost=0.43..349.14 rows=333 width=29) (actual time=0.007..0.135 rows=941 loops=1)
              Index Cond: (status = 'exception'::text)
              Buffers: shared hit=120
Planning Time: 0.017 ms
Execution Time: 0.301 ms
```

Full plans (JSON and text): `results/05-stale-statistics/`.

</details>

## 06. Correlated columns fool the row estimate

_A Singapore revenue report filters on country, currency, locale and timezone. They all say the same thing, but the planner multiplies their selectivities, estimates 1 customer instead of 40,000, and picks a serial nested loop._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 11.20 s | 1.00 s |
| Client wall clock (median of 3 / 5) | 10.83 s | 1.02 s |
| Waiting on block I/O | 8.29 s | 872.2 ms |
| Not waiting on I/O (CPU proxy) | 2.90 s | 132.1 ms |
| Buffers touched (hit + read) | 3,496.79 MB | 558.33 MB |
| Blocks read from outside shared_buffers | 104,955 | 33,014 |
| Rows read then discarded by filters | 2,200,290 | 1,959,942 |
| Worst row-estimate error (q-error) | 47,521x | 1.5x |
| Rows returned | 3 | 3 |
| Plan nodes | Aggregate, Gather, Index Scan, Nested Loop, Seq Scan, Sort | Aggregate, Gather Merge, Hash, Hash Join, Index Scan, Seq Scan, Sort |

**Result: 11,197.75 ms → 1,004.23 ms (11.2x on server execution time); result set identical; meets the 5x target.**

The fix adds no storage. It took 1.5 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Finance: Q3 revenue from Singapore customers, by loyalty tier. The filter
-- comes from a saved "Singapore" segment that pins every geo column.
SELECT c.tier, count(*) AS orders, sum(o.total_cents) AS revenue_cents
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE c.country  = 'SG'
  AND c.currency = 'SGD'
  AND c.locale   = 'en-SG'
  AND c.timezone = 'Asia/Singapore'
  AND o.created_at >= '2026-07-01 00:00:00+00:00'::timestamptz
GROUP BY c.tier
ORDER BY c.tier;
```

After:

```sql
-- Finance: Q3 revenue from Singapore customers, by loyalty tier. The filter
-- comes from a saved "Singapore" segment that pins every geo column.
SELECT c.tier, count(*) AS orders, sum(o.total_cents) AS revenue_cents
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE c.country  = 'SG'
  AND c.currency = 'SGD'
  AND c.locale   = 'en-SG'
  AND c.timezone = 'Asia/Singapore'
  AND o.created_at >= '2026-07-01 00:00:00+00:00'::timestamptz
GROUP BY c.tier
ORDER BY c.tier;
```

Fix:

```sql
-- Tell the planner the columns move together. `dependencies` records that
-- country determines currency and locale; `mcv` stores the most common
-- combinations with their real frequencies, which is what fixes this
-- estimate (SG/SGD/en-SG/Asia/Singapore is one combination, ~2% of rows).
CREATE STATISTICS customers_geo_stats (dependencies, mcv)
  ON country, currency, locale, timezone FROM customers;

-- Extended statistics are only populated by ANALYZE.
ANALYZE customers;
```

Plan before:

```
GroupAggregate  (cost=51364.76..51364.79 rows=1 width=48) (actual time=9146.919..9153.096 rows=3 loops=1)
  Group Key: c.tier
  Buffers: shared hit=351794 read=95795
  I/O Timings: shared read=6926.309
  ->  Sort  (cost=51364.76..51364.77 rows=1 width=16) (actual time=9146.688..9149.003 rows=47521 loops=1)
        Sort Key: c.tier
        Sort Method: quicksort  Memory: 3720kB
        Buffers: shared hit=351794 read=95795
        I/O Timings: shared read=6926.309
        ->  Nested Loop  (cost=1000.43..51364.75 rows=1 width=16) (actual time=0.840..9078.343 rows=47521 loops=1)
              Buffers: shared hit=351794 read=95795
              I/O Timings: shared read=6926.309
              ->  Gather  (cost=1000.00..51332.94 rows=1 width=16) (actual time=0.616..21.894 rows=40058 loops=1)
                    Workers Planned: 2
                    Workers Launched: 2
                    Buffers: shared hit=14 read=33652
                    I/O Timings: shared read=597.821
                    ->  Parallel Seq Scan on customers c  (cost=0.00..50332.84 rows=1 width=16) (actual time=0.142..280.096 rows=13353 loops=3)
                          Filter: ((country = 'SG'::text) AND (currency = 'SGD'::text) AND (locale = 'en-SG'::text) AND (timezone = 'Asia/Singapore'::text))
                          Rows Removed by Filter: 653314
                          Buffers: shared hit=14 read=33652
                          I/O Timings: shared read=597.821
              ->  Index Scan using orders_customer_id_idx on orders o  (cost=0.43..31.77 rows=4 width=16) (actual time=0.204..0.225 rows=1 loops=40058)
                    Index Cond: (customer_id = c.id)
                    Filter: (created_at >= '2026-07-01 00:00:00+00'::timestamp with time zone)
                    Rows Removed by Filter: 6
                    Buffers: shared hit=351780 read=62143
                    I/O Timings: shared read=6328.488
Planning:
  Buffers: shared hit=6 read=10
  I/O Timings: shared read=3.610
Planning Time: 4.097 ms
Execution Time: 9153.970 ms
```

Plan after:

```
Finalize GroupAggregate  (cost=114381.50..114382.29 rows=3 width=48) (actual time=768.233..790.959 rows=3 loops=1)
  Group Key: c.tier
  Buffers: shared hit=38584 read=32918
  I/O Timings: shared read=618.173
  ->  Gather Merge  (cost=114381.50..114382.20 rows=6 width=48) (actual time=768.061..790.803 rows=9 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=38584 read=32918
        I/O Timings: shared read=618.173
        ->  Sort  (cost=113381.47..113381.48 rows=3 width=48) (actual time=746.991..747.020 rows=3 loops=3)
              Sort Key: c.tier
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=38584 read=32918
              I/O Timings: shared read=618.173
              Worker 0:  Sort Method: quicksort  Memory: 25kB
              Worker 1:  Sort Method: quicksort  Memory: 25kB
              ->  Partial HashAggregate  (cost=113381.41..113381.45 rows=3 width=48) (actual time=746.456..746.470 rows=3 loops=3)
                    Group Key: c.tier
                    Batches: 1  Memory Usage: 24kB
                    Buffers: shared hit=38568 read=32918
                    I/O Timings: shared read=618.173
                    Worker 0:  Batches: 1  Memory Usage: 24kB
                    Worker 1:  Batches: 1  Memory Usage: 24kB
                    ->  Parallel Hash Join  (cost=50534.51..113241.24 rows=18690 width=16) (actual time=302.259..741.129 rows=15840 loops=3)
                          Hash Cond: (o.customer_id = c.id)
                          Buffers: shared hit=38568 read=32918
                          I/O Timings: shared read=618.173
                          ->  Parallel Index Scan using orders_created_at_idx on orders o  (cost=0.43..60075.82 rows=1002414 width=16) (actual time=0.105..303.278 rows=804887 loops=3)
                                Index Cond: (created_at >= '2026-07-01 00:00:00+00'::timestamp with time zone)
                                Buffers: shared hit=37790
                          ->  Parallel Hash  (cost=50333.72..50333.72 rows=16029 width=16) (actual time=301.647..301.656 rows=13353 loops=3)
                                Buckets: 65536  Batches: 1  Memory Usage: 2720kB
                                Buffers: shared hit=748 read=32918
                                I/O Timings: shared read=618.173
                                ->  Parallel Seq Scan on customers c  (cost=0.00..50333.72 rows=16029 width=16) (actual time=0.323..293.005 rows=13353 loops=3)
                                      Filter: ((country = 'SG'::text) AND (currency = 'SGD'::text) AND (locale = 'en-SG'::text) AND (timezone = 'Asia/Singapore'::text))
                                      Rows Removed by Filter: 653314
                                      Buffers: shared hit=748 read=32918
                                      I/O Timings: shared read=618.173
Planning:
  Buffers: shared hit=16
Planning Time: 0.736 ms
Execution Time: 791.326 ms
```

Full plans (JSON and text): `results/06-correlated-columns/`.

</details>

## 07. Hot-row lock contention

_A flash sale decrements one stock row inside a transaction that also waits on the payment provider. Every checkout queues on that row lock, so 32 concurrent buyers get the throughput of one._

### Measured

pgbench, 32 clients, 4 threads, 20 s per run.

| | Before | After |
|---|---:|---:|
| Throughput | 231 TPS | 7,942 TPS |
| Latency p50 / p95 / p99 | 98.506 / 391.93 / 637.598 ms | 3.794 / 5.683 / 8.831 ms |
| Backends waiting on a lock (mean of samples) | 28.92 of 32 | 3.95 of 32 |
| Backends idle in transaction (mean) | 0.85 | 4.36 |
| Failed transactions | 0 | 0 |

**Result: 231 TPS → 7,942 TPS (34.4x on throughput (TPS)); result set n/a; meets the 5x target.**

The fix adds no storage. It took 0.0 s to apply.

Other workloads affected by the fix:

| Workload | Before | After |
|---|---:|---:|
| Only move the payment call out of the transaction (still one stock row) | - | 2,425 TPS (p95 37.968 ms) |
| Only shard the stock row (payment call still inside the transaction) | - | 3,573 TPS (p95 23.918 ms) |

<details><summary>Statements, plans and fix</summary>

Before:

```
-- Checkout as first written: reserve stock, charge the card, record the order.
\set cust random(1, 2000000)
BEGIN;
UPDATE flash_sale_stock SET remaining = remaining - 1
WHERE sale_id = 1 AND slot = 0 AND remaining > 0;
-- The app now calls the payment provider (~2 ms) with the transaction open,
-- so the row lock taken by the UPDATE is held for the whole call.
\sleep 2 ms
INSERT INTO flash_sale_orders (sale_id, customer_id, slot) VALUES (1, :cust, 0);
COMMIT
```

After:

```
-- Checkout after the fix.
\set cust random(1, 2000000)
\set slot random(0, 15)
-- 1. Authorise the payment before opening the transaction (capture later).
\sleep 2 ms
-- 2. Short transaction; the hot UPDATE goes last so the row lock is held only
--    until COMMIT, and it hits one of 16 stock slots instead of a single row.
BEGIN;
INSERT INTO flash_sale_orders (sale_id, customer_id, slot) VALUES (1, :cust, :slot);
UPDATE flash_sale_stock SET remaining = remaining - 1
WHERE sale_id = 1 AND slot = :slot AND remaining > 0;
COMMIT
```

Fix:

```sql
-- Spread the sale's stock over 16 slots. A checkout picks a slot at random,
-- so concurrent buyers usually lock different rows. Reading total stock is
-- a sum over the slots. When a slot runs dry the app retries another slot;
-- with 16 slots the last few units need that fallback, which is the price of
-- the extra concurrency.
INSERT INTO flash_sale_stock (sale_id, slot, remaining)
SELECT 1, s, 0 FROM generate_series(1, 15) AS s;

UPDATE flash_sale_stock AS f
SET remaining = t.total / 16 + CASE WHEN f.slot = 0 THEN mod(t.total, 16) ELSE 0 END
FROM (SELECT sum(remaining) AS total FROM flash_sale_stock WHERE sale_id = 1) AS t
WHERE f.sale_id = 1;

-- The second half of the fix is in after.pgbench: the payment call moves out
-- of the transaction and the hot UPDATE moves to just before COMMIT.
```

Full plans (JSON and text): `results/07-hot-row-lock-contention/`.

</details>

## 08. Deep OFFSET pagination

_An admin list pages through orders with OFFSET; page 4,000 reads and discards 200,000 rows to return 50._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 32.5 ms | 0.03 ms |
| Client wall clock (median of 9 / 9) | 33.3 ms | 0.92 ms |
| Waiting on block I/O | 0.00 ms | 0.00 ms |
| Not waiting on I/O (CPU proxy) | 32.5 ms | 0.03 ms |
| Buffers touched (hit + read) | 23.74 MB | 0.04 MB |
| Blocks read from outside shared_buffers | 0 | 0 |
| Rows read then discarded by filters | 0 | 0 |
| Worst row-estimate error (q-error) | 1.0x | 1.0x |
| Rows returned | 50 | 50 |
| Plan nodes | Incremental Sort, Index Scan, Limit | Index Scan, Limit |

**Result: 32.52 ms → 0.03 ms (1,301x on server execution time); result set identical; meets the 5x target.**

Storage cost of the fix:

| Relation | Kind | Change | Size after |
|---|---|---:|---:|
| `orders_created_at_id_idx` | index (added) | +450.6 MB | +450.6 MB |
| `orders_created_at_idx` | index (dropped) | -321.3 MB | 0 B |

Net index change +129.3 MB, net table change 0 B. The fix took 14.8 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Admin "all orders" list, newest first, 50 per page; this is page 4,001.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
ORDER BY created_at DESC, id DESC
OFFSET 200000
LIMIT 50;
```

After:

```sql
-- Keyset pagination: continue strictly after the last row the client saw.
-- The row comparison matches the index column order, so it is a single
-- index condition and the scan starts exactly where the page begins.
SELECT id, customer_id, status, total_cents, created_at
FROM orders
WHERE (created_at, id) < ('2026-09-23 16:12:45.374899+00:00'::timestamptz, 14800001)
ORDER BY created_at DESC, id DESC
LIMIT 50;
```

Fix:

```sql
-- (created_at, id) gives a total order, so the tie-break on id needs no sort
-- and the row comparison in after.sql is one index condition.
CREATE INDEX orders_created_at_id_idx ON orders (created_at, id);

-- The old single-column index is a prefix of the new one.
DROP INDEX orders_created_at_idx;
```

Plan before:

```
Limit  (cost=14661.34..14665.01 rows=50 width=41) (actual time=52.375..52.384 rows=50 loops=1)
  Buffers: shared hit=3039
  ->  Incremental Sort  (cost=0.47..1099565.64 rows=15000000 width=41) (actual time=0.014..47.328 rows=200050 loops=1)
        Sort Key: created_at DESC, id DESC
        Presorted Key: created_at
        Full-sort Groups: 6252  Sort Method: quicksort  Average Memory: 27kB  Peak Memory: 27kB
        Buffers: shared hit=3039
        ->  Index Scan Backward using orders_created_at_idx on orders  (cost=0.43..424565.64 rows=15000000 width=41) (actual time=0.005..20.653 rows=200051 loops=1)
              Buffers: shared hit=3039
Planning Time: 0.043 ms
Execution Time: 52.400 ms
```

Plan after:

```
Limit  (cost=0.43..2.06 rows=50 width=41) (actual time=0.008..0.016 rows=50 loops=1)
  Buffers: shared hit=5
  ->  Index Scan Backward using orders_created_at_id_idx on orders  (cost=0.43..481369.14 rows=14794703 width=41) (actual time=0.007..0.013 rows=50 loops=1)
        Index Cond: (ROW(created_at, id) < ROW('2026-09-23 16:12:45.374899+00'::timestamp with time zone, 14800001))
        Buffers: shared hit=5
Planning:
  Buffers: shared hit=4
Planning Time: 0.047 ms
Execution Time: 0.026 ms
```

Full plans (JSON and text): `results/08-deep-offset-pagination/`.

</details>

## 09. Leading-wildcard search

_Support searches customers by name with ILIKE '%...%'; no B-tree can serve a leading wildcard, so every search scans 2M rows._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 366.5 ms | 12.2 ms |
| Client wall clock (median of 9 / 9) | 362.1 ms | 12.6 ms |
| Waiting on block I/O | 71.0 ms | 0.00 ms |
| Not waiting on I/O (CPU proxy) | 295.5 ms | 12.2 ms |
| Buffers touched (hit + read) | 263.91 MB | 5.72 MB |
| Blocks read from outside shared_buffers | 32,610 | 0 |
| Rows read then discarded by filters | 1,999,764 | 0 |
| Worst row-estimate error (q-error) | 1.0x | 1.0x |
| Rows returned | 25 | 25 |
| Plan nodes | Gather Merge, Limit, Seq Scan, Sort | Bitmap Heap Scan, Bitmap Index Scan, Limit, Sort |

**Result: 366.51 ms → 12.23 ms (30.0x on server execution time); result set identical; meets the 5x target.**

Storage cost of the fix:

| Relation | Kind | Change | Size after |
|---|---|---:|---:|
| `customers_full_name_trgm_idx` | index (added) | +46.1 MB | +46.1 MB |

Net index change +46.1 MB, net table change 0 B. The fix took 5.5 s to apply.

HypoPG what-if before building anything: estimated plan cost 45,088 → 45,088; planner would not use the hypothetical index <13561>btree_customers_full_name (~69 MB).

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Support console: an agent types part of a customer's name.
SELECT id, full_name, email, country, signup_at
FROM customers
WHERE full_name ILIKE '%sakura kusumawardani%'
ORDER BY signup_at DESC
LIMIT 25;
```

After:

```sql
-- Support console: an agent types part of a customer's name.
SELECT id, full_name, email, country, signup_at
FROM customers
WHERE full_name ILIKE '%sakura kusumawardani%'
ORDER BY signup_at DESC
LIMIT 25;
```

Fix:

```sql
-- Trigram GIN index: ILIKE '%term%' becomes a lookup of the term's trigrams.
CREATE INDEX customers_full_name_trgm_idx
  ON customers USING gin (full_name gin_trgm_ops);
```

Plan before:

```
Limit  (cost=45085.02..45087.94 rows=25 width=62) (actual time=360.730..361.979 rows=25 loops=1)
  Buffers: shared hit=1266 read=32514
  I/O Timings: shared read=65.609
  ->  Gather Merge  (cost=45085.02..45102.99 rows=154 width=62) (actual time=360.729..361.976 rows=25 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=1266 read=32514
        I/O Timings: shared read=65.609
        ->  Sort  (cost=44085.00..44085.19 rows=77 width=62) (actual time=359.350..359.352 rows=20 loops=3)
              Sort Key: signup_at DESC
              Sort Method: top-N heapsort  Memory: 31kB
              Buffers: shared hit=1266 read=32514
              I/O Timings: shared read=65.609
              Worker 0:  Sort Method: top-N heapsort  Memory: 31kB
              Worker 1:  Sort Method: top-N heapsort  Memory: 31kB
              ->  Parallel Seq Scan on customers  (cost=0.00..44082.83 rows=77 width=62) (actual time=5.706..359.247 rows=79 loops=3)
                    Filter: (full_name ~~* '%sakura kusumawardani%'::text)
                    Rows Removed by Filter: 666588
                    Buffers: shared hit=1152 read=32514
                    I/O Timings: shared read=65.609
Planning Time: 0.196 ms
Execution Time: 361.995 ms
```

Plan after:

```
Limit  (cost=455.04..455.11 rows=25 width=62) (actual time=10.777..10.780 rows=25 loops=1)
  Buffers: shared hit=732
  ->  Sort  (cost=455.04..455.50 rows=184 width=62) (actual time=10.777..10.778 rows=25 loops=1)
        Sort Key: signup_at DESC
        Sort Method: top-N heapsort  Memory: 31kB
        Buffers: shared hit=732
        ->  Bitmap Heap Scan on customers  (cost=246.51..449.85 rows=184 width=62) (actual time=10.546..10.743 rows=236 loops=1)
              Recheck Cond: (full_name ~~* '%sakura kusumawardani%'::text)
              Heap Blocks: exact=236
              Buffers: shared hit=732
              ->  Bitmap Index Scan on customers_full_name_trgm_idx  (cost=0.00..246.47 rows=184 width=0) (actual time=10.529..10.529 rows=236 loops=1)
                    Index Cond: (full_name ~~* '%sakura kusumawardani%'::text)
                    Buffers: shared hit=496
Planning:
  Buffers: shared hit=1
Planning Time: 0.118 ms
Execution Time: 10.787 ms
```

Full plans (JSON and text): `results/09-leading-wildcard-search/`.

</details>

## 10. Exact counts on every page load

_An admin header recomputes order counts by status over 15M rows on every page view. The fix is a change to what the page asks for, not to the planner._

### Measured

| | Before | After |
|---|---:|---:|
| Server execution time (pg_stat_statements, mean) | 1.60 s | 4.70 ms |
| Client wall clock (median of 7 / 7) | 1.64 s | 4.92 ms |
| Waiting on block I/O | 1.89 s | 0.00 ms |
| Not waiting on I/O (CPU proxy) | -286.78 ms | 4.70 ms |
| Buffers touched (hit + read) | 1,205.76 MB | 2.9 MB |
| Blocks read from outside shared_buffers | 130,043 | 0 |
| Rows read then discarded by filters | 0 | 0 |
| Worst row-estimate error (q-error) | 1.5x | 33x |
| Rows returned | 6 | 6 |
| Plan nodes | Aggregate, Gather Merge, Seq Scan, Sort | Aggregate, Append, Index Scan, Seq Scan, Sort, Subquery Scan |

**Result: 1,599.31 ms → 4.70 ms (341x on server execution time); result set identical; meets the 5x target.**

Storage cost of the fix:

| Relation | Kind | Change | Size after |
|---|---|---:|---:|
| `daily_order_stats` | table (added) | +200.0 KB | +200.0 KB |
| `daily_order_stats_pkey` | index (added) | +144.0 KB | +144.0 KB |

Net index change +144.0 KB, net table change +200.0 KB. The fix took 5.2 s to apply.

<details><summary>Statements, plans and fix</summary>

Before:

```sql
-- Admin dashboard header, run on every page view.
SELECT status, count(*) AS orders, sum(total_cents) AS revenue_cents
FROM orders
GROUP BY status
ORDER BY status;
```

After:

```sql
-- Same answer: closed days from the rollup, today counted live. The live
-- part is an index range scan over one day of orders.
SELECT status, sum(orders)::bigint AS orders, sum(revenue_cents)::numeric AS revenue_cents
FROM (
    SELECT status, orders, revenue_cents
    FROM daily_order_stats
    UNION ALL
    SELECT status, count(*), sum(total_cents)
    FROM orders
    WHERE created_at >= '2026-09-30 00:00:00+00:00'::timestamptz
    GROUP BY status
) s
GROUP BY status
ORDER BY status;
```

Fix:

```sql
-- The rollup a scheduled job maintains. Statuses change for ~14 days after an
-- order is placed, so the nightly job recomputes the trailing 14 days and
-- leaves older days alone. This statement is the job's one-off backfill.
CREATE TABLE daily_order_stats (
    day           date   NOT NULL,
    status        text   NOT NULL,
    orders        bigint NOT NULL,
    revenue_cents bigint NOT NULL,
    PRIMARY KEY (day, status)
);

INSERT INTO daily_order_stats
SELECT (created_at AT TIME ZONE 'UTC')::date, status, count(*), sum(total_cents)
FROM orders
WHERE created_at < {{rollup_through}}
GROUP BY 1, 2;

ANALYZE daily_order_stats;
```

Plan before:

```
Finalize GroupAggregate  (cost=264696.18..264697.77 rows=6 width=49) (actual time=1674.869..1690.892 rows=6 loops=1)
  Group Key: status
  Buffers: shared hit=24390 read=129947
  I/O Timings: shared read=1622.210
  ->  Gather Merge  (cost=264696.18..264697.58 rows=12 width=49) (actual time=1674.820..1690.845 rows=18 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=24390 read=129947
        I/O Timings: shared read=1622.210
        ->  Sort  (cost=263696.15..263696.17 rows=6 width=49) (actual time=1671.602..1671.608 rows=6 loops=3)
              Sort Key: status
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=24390 read=129947
              I/O Timings: shared read=1622.210
              Worker 0:  Sort Method: quicksort  Memory: 25kB
              Worker 1:  Sort Method: quicksort  Memory: 25kB
              ->  Partial HashAggregate  (cost=263696.00..263696.08 rows=6 width=49) (actual time=1671.478..1671.481 rows=6 loops=3)
                    Group Key: status
                    Batches: 1  Memory Usage: 24kB
                    Buffers: shared hit=24374 read=129947
                    I/O Timings: shared read=1622.210
                    Worker 0:  Batches: 1  Memory Usage: 24kB
                    Worker 1:  Batches: 1  Memory Usage: 24kB
                    ->  Parallel Seq Scan on orders  (cost=0.00..216821.00 rows=6250000 width=17) (actual time=0.058..896.483 rows=5000000 loops=3)
                          Buffers: shared hit=24374 read=129947
                          I/O Timings: shared read=1622.210
Planning Time: 0.255 ms
Execution Time: 1691.244 ms
```

Plan after:

```
Sort  (cost=1249.02..1249.52 rows=200 width=72) (actual time=6.017..6.018 rows=6 loops=1)
  Sort Key: "*SELECT* 1".status
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=371
  ->  HashAggregate  (cost=1237.87..1241.37 rows=200 width=72) (actual time=6.013..6.015 rows=6 loops=1)
        Group Key: "*SELECT* 1".status
        Batches: 1  Memory Usage: 40kB
        Buffers: shared hit=371
        ->  Append  (cost=0.00..1213.12 rows=3300 width=72) (actual time=0.003..5.641 rows=3298 loops=1)
              Buffers: shared hit=371
              ->  Subquery Scan on "*SELECT* 1"  (cost=0.00..99.11 rows=3294 width=49) (actual time=0.003..0.515 rows=3294 loops=1)
                    Buffers: shared hit=25
                    ->  Seq Scan on daily_order_stats  (cost=0.00..57.94 rows=3294 width=25) (actual time=0.002..0.214 rows=3294 loops=1)
                          Buffers: shared hit=25
              ->  HashAggregate  (cost=1097.43..1097.51 rows=6 width=49) (actual time=4.963..4.963 rows=4 loops=1)
                    Group Key: orders.status
                    Batches: 1  Memory Usage: 24kB
                    Buffers: shared hit=346
                    ->  Index Scan using orders_created_at_idx on orders  (cost=0.43..882.93 rows=28600 width=17) (actual time=0.003..1.884 rows=27385 loops=1)
                          Index Cond: (created_at >= '2026-09-30 00:00:00+00'::timestamp with time zone)
                          Buffers: shared hit=346
Planning:
  Buffers: shared hit=4
Planning Time: 0.041 ms
Execution Time: 6.030 ms
```

Full plans (JSON and text): `results/10-exact-count-dashboard/`.

</details>
