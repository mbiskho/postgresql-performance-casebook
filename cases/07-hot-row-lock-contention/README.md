# Hot-row lock contention

**Symptom.** During a flash sale, checkout latency climbs to hundreds of
milliseconds and throughput stays flat however many application servers are
added. Database CPU is low. Nothing in the slow query log looks expensive.

**What the plan shows.** Each statement is trivial: a one-row `UPDATE` by
primary key and a one-row `INSERT`. The cost isn't in any plan. Sampling
`pg_stat_activity` during the run shows almost every backend with
`wait_event_type = 'Lock'`, queued behind whichever checkout currently holds
the row lock on the sale's single `flash_sale_stock` row. The holder isn't
doing database work at all: it is `idle in transaction`, waiting for the
payment provider.

**Why.** An `UPDATE` holds its row lock until the transaction ends. The
checkout reserves stock first and then calls the payment provider (simulated
with a client-side 2 ms `\sleep` inside the transaction, which is what a
synchronous HTTP call looks like from the database). So every checkout holds
the one row everyone needs for the length of an external API call, and the
whole sale runs one checkout at a time.

**Fix.** Two changes, each measured on its own below.

1. *Shorten the critical section.* Authorise the payment before opening the
   transaction, and capture it after commit. Inside the transaction, do the
   hot `UPDATE` last, so the lock is held only until `COMMIT`.
2. *Spread the hot row.* Split the sale's stock into 16 slots and have each
   checkout decrement a random one. The total is a `sum()` over slots, and a
   checkout that finds its slot empty retries another.

Neither change is enough alone: the short transaction still serialises on
one row, and the sharded row still holds each lock across a payment call.
Together they remove the queue.

**Trade-offs.** Sharding makes "stock remaining" a sum, and a sale can
report sold out in one slot while another still has units, so the last few
units need the retry path. Taking payment outside the transaction means you
need a compensation step (void the authorisation) when the stock update
fails. That is the normal shape for a payment flow anyway.

**Catching it in production.** `log_lock_waits = on` (enabled here, with
`deadlock_timeout = 200ms`) logs every wait longer than `deadlock_timeout`.
In `pg_stat_activity`, many sessions waiting on `Lock / transactionid` or
`Lock / tuple`, and some `idle in transaction`, means a transaction is being
held open around work that isn't database work.
