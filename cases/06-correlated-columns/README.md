# Correlated columns fool the row estimate

**Symptom.** A quarterly revenue report for Singapore customers takes about ten
seconds. The same report for a larger country, filtered differently, takes
well under one.

**What the plan shows.** A `Nested Loop` driven by a sequential scan of
`customers` estimated to return **1 row**. It actually returns **40,058**.
For each of those customers the plan probes `orders_customer_id_idx` and
fetches that customer's orders from the heap, so there are 40,058 index scans
and ~280,000 random heap fetches across a 1.2 GB table. Because the planner
expected one row, it ran the whole join serially above the `Gather`, with no
parallel workers.

**Why.** The saved "Singapore" segment filters on `country = 'SG' AND currency
= 'SGD' AND locale = 'en-SG' AND timezone = 'Asia/Singapore'`. Each predicate
alone matches about 2% of customers, and together they still match about
2%, because they are four ways of saying the same thing. The planner assumes
columns are independent and multiplies the selectivities: 0.02⁴ × 2,000,000 ≈
0.3 rows, rounded up to 1. With one outer row a nested loop is the cheapest
plan; with 40,000 it is the most expensive one on the menu.

**Fix.** `CREATE STATISTICS customers_geo_stats (dependencies, mcv) ON country,
currency, locale, timezone FROM customers`, then `ANALYZE`. The `mcv` list
stores how often each combination actually occurs, so the estimate becomes
~43,000 rows. The planner then picks a parallel hash join: it scans the
quarter's orders through the `created_at` index once and hashes the Singapore
customers. Nothing about the query or the indexes changes.

**Trade-offs.** Extended statistics cost a little `ANALYZE` time and a few
kilobytes of catalog space. They only help predicates they can model:
equality and `IN` on the listed columns, plus range clauses when the values
fall in the MCV list. The other honest fix is to stop filtering on redundant
columns. `country = 'SG'` alone gets a correct estimate. That fix belongs
in the segment builder, and the extended statistics protect every other
query that does the same thing.

**Catching it in production.** A plan node estimated at 1 row (often exactly 1)
that returns thousands, sitting on the outer side of a nested loop. Queries
that filter on several columns derived from one source, such as geography,
product taxonomy, or plan and tier, are the usual suspects.
