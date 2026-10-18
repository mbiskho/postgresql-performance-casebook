# Deep OFFSET pagination

**Symptom.** The admin "all orders" list is instant on page 1 and slower on
every page after. Page 4,001 (50 rows per page) takes tens of milliseconds.
Crawlers and export scripts that walk every page put a load on the database
that grows quadratically with the number of pages.

**What the plan shows.** `Index Scan Backward using orders_created_at_idx`,
then an `Incremental Sort` to break ties on `id`, then a `Limit` that throws
away the first 200,000 rows and returns the next 50. PostgreSQL has to produce
and sort every one of the skipped rows, because `OFFSET` is defined in terms of
the rows before it.

**Why.** An `OFFSET` can't jump to a position, so it always has to count the
rows before it. The index helps produce rows in order, but every page still
starts from the top.

**Fix.** Keyset (cursor) pagination. The "next" link carries the
`(created_at, id)` of the last row on the current page, and the query asks for
rows strictly before it: `WHERE (created_at, id) < ($1, $2) ORDER BY
created_at DESC, id DESC LIMIT 50`. A new `(created_at, id)` index matches
that row comparison exactly, so it becomes a single index condition, and the
scan starts where the page begins and reads 50 entries. The old `(created_at)`
index is a prefix of the new one and is dropped.

**Trade-offs.** Keyset pagination can't jump to "page 4,001" directly. It can
only go forwards and backwards from a known row. That is usually fine,
because people don't jump to arbitrary deep page numbers, and it also fixes a
correctness problem: with `OFFSET`, rows inserted while someone pages through
the list shift everything, and rows get skipped or shown twice. If a product
really needs numbered pages, cap the depth.

**Catching it in production.** Statements with `OFFSET` in
`pg_stat_statements` whose mean time grows over the day, and plans where the
`Limit` node's input row count is far larger than its output.
