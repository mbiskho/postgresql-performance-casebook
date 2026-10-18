# Partition key does not match the access path

**Symptom.** `page_views` was hash-partitioned 16 ways by `user_id`, because
the first feature built on it was a per-user activity feed. Since then the
growth dashboard ("top pages in the last 3 days") and the 13-month retention
job have become the table's main consumers, and both are slow.

**What the plan shows.** The dashboard query filters on `viewed_at`. Hash
partitions by user can't be pruned by time, so the plan is a parallel
`Append` of sequential scans over all 16 partitions, reading 10 million rows
to aggregate about 115,000. The retention job is a `DELETE ... WHERE
viewed_at < ...` that also visits every partition, writes WAL for every
deleted row, and leaves dead tuples for autovacuum.

**Why.** Partitioning only helps a query that constrains the partition key.
The key was chosen for one query, the feed, and every other workload pays for
that choice.

**Fix.** Re-partition by `RANGE (viewed_at)`, one partition per month, copying
the data in time order. The `(user_id, viewed_at)` index is created on the
parent, so every month gets a local copy and the feed keeps working. The
dashboard query is unchanged; the planner now prunes to the current month's
partition. Retention becomes `DROP TABLE` on old partitions, a catalog
operation that takes milliseconds and writes no per-row WAL. In production the
copy would be an online migration (dual writes or logical replication, then a
short lock for the rename); the case does the copy directly because the data
is static.

**Trade-offs.**

- *The feed got slightly slower.* With hash partitions it touched one
  partition; now it walks monthly partitions newest first until it has 50
  rows. That's fine for active users and worse for users whose last visit was
  long ago. It is measured below under "other workloads".
- *A BRIN index might be enough for the dashboard on its own.* Each hash
  partition is still append-ordered by `viewed_at`, which is exactly what a
  BRIN index needs. Measured as an alternative, it answers the dashboard query
  as fast as the repartition does, for a few hundred kilobytes of index. If
  the dashboard were the only time-based consumer, that's the change I would
  ship. Retention is what justifies the repartition: no index turns a 13-month
  `DELETE` into a `DROP`.

**Catching it in production.** Plans with an `Append` over every partition and
no "Subplans Removed" line. A retention job whose runtime and WAL volume grow
with the table. Autovacuum constantly working through dead tuples on an
append-only table, which shouldn't have many.
