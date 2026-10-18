# Non-sargable date predicate

**Symptom.** Finance exports one day of orders at a time, and each export takes
over a second regardless of which day is asked for. There is an index on
`orders.created_at`, and `EXPLAIN` shows nobody using it.

**What the plan shows.** A parallel sequential scan of all 15 million orders,
with ~1.2 GB of buffers touched. Each worker computes `(created_at AT TIME ZONE
'UTC')::date` for every row and compares it with the requested day, then
throws away all but ~20,000 of them.

**Why.** An index on `created_at` is ordered by `created_at`. It can't answer
questions about an expression *of* `created_at`, because the planner doesn't
know that the cast to a date is monotonic. Wrapping an indexed column in a
function or cast makes the predicate non-sargable, meaning it can't be used as
a search argument. The same applies to `lower(email) = ...`, `date_trunc(...)
= ...`, `col + 1 = ...` and implicit casts the driver adds when parameter and
column types differ.

The time zone makes this bug easy to write and hard to see. A bare
`created_at::date` depends on the session's `TimeZone` setting, so the
"obvious" version can return a different day's orders depending on which
connection runs it. Both versions here name UTC explicitly.

**Fix.** Rewrite the predicate as a half-open range on the raw column:
`created_at >= day AND created_at < day + 1`. It means the same thing, the
existing index bounds the scan to one day, and no schema change is needed.

**Trade-offs.** The alternative, an expression index on the UTC date, was
measured too. It is just as fast for this query and needs no application
change. It is also a second full index on a 15M-row table that every insert
pays for, and it only helps queries that spell the expression exactly the same
way. Use an expression index when you can't change the query; when you can,
rewrite it.

**Catching it in production.** Look for a sequential scan on a large table
whose `Filter:` line wraps an indexed column in a function. In
`pg_stat_user_tables`, a growing `seq_tup_read` on a table that should be read
by index is the aggregate symptom.
