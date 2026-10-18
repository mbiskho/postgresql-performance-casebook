# Missing composite index

**Symptom.** The account page has an "open orders" panel: the 20 most recent
orders that are still pending, paid or shipped. It is fine for most customers
and slow for the largest ones. The heaviest account in the dataset, a reseller
with about 19,800 orders, is the one support keeps raising tickets about.

**What the plan shows.** The only index that mentions the customer is
`orders_customer_id_idx (customer_id)`. PostgreSQL uses it to find all ~19,800
of the account's orders, then visits the heap for every one of them. Orders
are stored in the order they were placed, so one customer's rows are spread
over ~18,500 different pages. Only then does it apply the status filter, which
discards ~19,400 rows, and sort the ~400 survivors to keep 20. The work grows
with the size of the account's history, not with the 20 rows the panel shows.
On a cold cache the same plan spent most of a second reading those pages
from disk.

**Why.** The index finds the customer but not the subset or the order. Every
other condition is applied after the expensive part, fetching the rows, has
already happened.

**Fix.** A composite index in the order *equality columns, then the sort
column*: `(customer_id, status, created_at DESC)`. For the three statuses in
the `IN` list, PostgreSQL 16 runs one short range scan per status, reads only
in-flight orders, and sorts a few hundred index entries. The old
`(customer_id)` index is a prefix of the new one, so it is dropped in the same
change. That keeps write amplification about where it was and pays back most
of the new index's size.

HypoPG was used first, to confirm that the planner would pick the index before
spending time building ~450 MB of it.

**Trade-offs.** Two other indexes were measured. They are listed under
"Alternatives" below.

- *`(customer_id, created_at DESC)` without status* serves every tab of the
  account page from one index. For "open orders" on a big account, though, it
  walks back through delivered orders until it has found 20 in flight.
- *A partial index on in-flight orders only* is much smaller and just as fast
  for this panel, because fewer than 1% of orders are in flight. It is the
  better choice if this panel is the only hot path. It was not chosen here
  because the other account-page queries still need a `customer_id` index, and
  the composite replaces that index rather than sitting next to it.

**Catching it in production.** In `pg_stat_statements` this statement has a
high `shared_blks_read` per call and a mean time that varies a lot between
customers. In `auto_explain`, the clearest sign is `Rows Removed by Filter`
two orders of magnitude larger than the rows returned. Statements that are
fast in staging and slow for big tenants in production often look like this.
