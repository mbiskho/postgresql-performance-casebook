# Exact counts on every page load

**Symptom.** Every page of the admin console renders a header with order counts
and revenue by status. Each page view costs well over a second of database
time, and more admins means proportionally more load. The query is correct,
well written, and already uses a parallel plan.

**What the plan shows.** A parallel sequential scan of all 15 million orders
feeding a hash aggregate. There's no index to add: the query really does
need every row, because it asks for exact totals over all of history.

**Why.** Not a planner problem. The question is too expensive to ask on every
page view. Counting is linear in table size in PostgreSQL. MVCC means there
is no stored row count to read, and even an index-only `count(*)` has to
visit every index entry and consult the visibility map.

**Fix: change what the page asks for.** History doesn't change, so it
shouldn't be recounted. A scheduled job maintains `daily_order_stats (day,
status, orders, revenue)`. Because order statuses keep changing for about two
weeks after an order is placed, the job recomputes the trailing 14 days each
night and leaves older days alone. The header sums the rollup and counts
today's orders live through the `created_at` index. The result is identical to
the original query, which the runner checks, and it reads a few thousand rollup
rows and one day of orders instead of 15 million rows.

That this case is in a database casebook at all is the point. The fix is a
product and data-model decision: a rollup owned by a job, plus an agreement
that the header is "exact as of the last job run, plus today". In many
products an approximate number is acceptable too. `reltuples` from `pg_class`,
or the last value cached for a minute, costs nothing.

**Trade-offs.** One more table and one more job to monitor. A missed run
makes the header stale, so the job should alert. Status changes older than
the 14-day window aren't reflected until a full rebuild, which is cheap to
run weekly. A trigger-maintained counter was deliberately not used: it would
turn every order insert into an update of a few hot rows, which is the
contention problem from case 07.

**Catching it in production.** High `calls` combined with high
`mean_exec_time` on an aggregate over a large table. When a statement's
result barely changes between calls, its work should be precomputed.
