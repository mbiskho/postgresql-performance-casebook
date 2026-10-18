# N+1 queries from an ORM loop

**Symptom.** The order-history page is slow, but nothing shows up in the slow
query log. Every query on the page takes well under a millisecond on the
server.

**What the plan shows.** Nothing is wrong with any single plan. The page runs
one query for the customer's 50 most recent orders, then one query per order
for its line items, then one query per line item for the product. That is
about 180 statements, each an index lookup, each with its own network round
trip, driver overhead, parse, plan and execute. The time goes into the gaps
between the queries.

**Why.** Lazy loading. The template iterates over `order.items` and
`item.product`, and the ORM loads each relationship the first time it is
touched. In development, with three orders and a local database, the page is
instant.

**Fix.** Load the page in one statement: the 50 orders in a subquery, joined to
items and products, with items folded into a JSON array per order. The
indexes the lazy loads were using are the same ones the join uses, so the
database needs no change. In ORM terms this is eager loading
(`select_related`/`prefetch_related`, `includes`, `JOIN FETCH`). Two or three
batched queries using `WHERE order_id = ANY($1)` get most of the same benefit
when one big join is awkward.

**Trade-offs.** One statement returns a wider result, and the JSON
aggregation does work the application used to do. Neither matters next to the
round trips. The measurement here understates the problem: the client and
the database share a Docker bridge network, with round trips of a fraction of
a millisecond. With a database in another availability zone (~0.5–1 ms per
round trip) the N+1 page gets slower by roughly that much times 180, while the
one-query version barely changes.

**Catching it in production.** `pg_stat_statements` shows statements with huge
`calls` counts and tiny `mean_exec_time`. In APM traces, one request makes
dozens or hundreds of identical-looking spans. Ranking `pg_stat_statements` by
`calls`, not by mean time, finds them.
