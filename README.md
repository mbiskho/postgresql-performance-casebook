# PostgreSQL Performance Casebook

Ten slow queries, fixed, with the plans shown.

A runnable casebook on a 64-million-row dataset whose distributions are skewed
the way production data is. Each case puts the database into a broken state,
measures the slow statement, applies a fix, measures again, checks that the
answer didn't change, and rolls the fix back. One command reproduces all of it
from an empty container, so you can run the slow version yourself and see how
bad it is.

- **The casebook:** [reports/casebook.md](reports/casebook.md), or open
  [index.html](index.html) in a browser. Both are generated from `results/`.
- **The decision guide:** [docs/indexing-decision-guide.md](docs/indexing-decision-guide.md),
  covering indexing, statistics and partitioning, and when the database is
  the wrong place to fix a problem.

## Results

<!-- results:start -->
<!-- results:end -->

## Run it

Requirements: Docker with about 4 GB of RAM for the VM and about 20 GB of free
disk. Nothing needs to be installed on the host: seeding, measurement and
pgbench all run in a tool container built from the same PostgreSQL image.

```bash
make all            # clean volume -> PostgreSQL 16 -> seed 64M rows -> run 10 cases -> report
```

Or step by step:

```bash
make up             # PostgreSQL 16 on localhost:5434 (user/db: casebook)
make seed           # ~5 minutes, ~7 GB; SCALE=0.1 for a 6M-row version
make check          # extensions, preload libraries, seed status
make case CASE=05   # one case, printed as it runs
make run            # all cases plus a pgbench replay of each, then the report
make report         # rebuild reports/casebook.md and index.html from results/
make psql           # poke at it yourself
make slowlog        # plans auto_explain logged for statements over 500 ms
make observability  # postgres_exporter + Prometheus (:9094) + Grafana (:3004)
make smoke          # end-to-end check on a tiny dataset in a separate project
```

To feel a case rather than read about it, run the slow statement yourself:

```bash
make psql
\timing on
\i cases/06-correlated-columns/before.sql   -- after replacing {{quarter_start}}
```

The rendered statements, with parameters filled in, are saved in
`results/<case>/before.sql` and `after.sql` after a run.

## What is in the box

```
docker/postgres/        PostgreSQL 16 + HypoPG; pg_stat_statements, auto_explain, pg_trgm
docker/runner/          tool image: Python 3.11, psycopg 3, pgbench, psql
sql/schema.sql          the schema as "shipped": PKs, a unique email, single-column FK and created_at indexes
casebook/
  seed.py               deterministic generator, server-side INSERT ... SELECT, chunked and seeded
  refdata.py            weighted countries, cities, names, categories, pages
  measure.py            setup -> measure -> fix -> measure -> teardown; plan, N+1 and pgbench drivers
  plans.py              EXPLAIN JSON -> timings, buffers, rows removed, q-error per node
  report.py             results/ -> reports/casebook.md + index.html
cases/NN-*/             one directory per case:
  case.toml             title, driver, parameters, alternatives, extra workloads
  README.md             symptom, plan, why, fix, trade-offs, how to catch it in production
  setup.sql             puts the database into the broken state (if the seed alone doesn't)
  before.sql            the slow statement
  hypo.sql              HypoPG what-if, where it applies
  fix.sql               the change
  after.sql             the statement after the fix (when the fix is a rewrite)
  teardown.sql          undoes the fix so the case can be rerun
  alt_*.sql             alternative fixes that were measured and not chosen
results/NN-*/           plans (JSON + text), rendered SQL, pgbench output, result.json
observability/          Prometheus scrape config and a provisioned Grafana dashboard
docs/                   indexing and partitioning decision guide
```

## The dataset

`make seed` generates about 64 million rows server-side in about five minutes.
At scale 1.0:

| Table | Rows | Shape that matters |
|---|---:|---|
| `customers` | 2,000,000 | Country mix skewed (Indonesia 30%, Singapore 2%); currency, locale and timezone are functionally dependent on country and city |
| `products` | 100,000 | Zipf-like popularity, with popular products scattered across the id space |
| `orders` | 15,000,000 | Power-law activity per customer (top account ~20k orders, median 5, ~3% of customers with none); 3% guest checkouts with NULL customer; growth over three years; heap append-ordered by `created_at`; status depends on age |
| `order_items` | ~37,300,000 | Geometric number of lines per order (mean ~2.5), stored next to their order |
| `page_views` | 10,000,000 | Hash-partitioned 16 ways by user, append-ordered by time within each partition |

Uniform random data produces plans you'd never see in production. Most of
these cases only exist because of the skew: the heavy account in case 01, the
dependent columns in case 06, the time-ordered heap behind the BRIN
alternative in case 04. Each chunk calls `setseed()` with its own seed, so a
given scale always produces the same rows. Per-case state (the stale-stats
event table, the flash-sale stock row) is rebuilt by each case's `setup.sql`.

## How a case is measured

1. `setup.sql` builds the broken state. Parameters are resolved by SQL
   against the data (for example "the heaviest account", or "the row just
   before page 4,001"), so cases adapt to the seed scale.
2. Where it applies, HypoPG asks the planner whether it would use the proposed
   index, before anything is built.
3. The slow statement gets warm-up runs and then timed runs, each inside
   `BEGIN ... ROLLBACK`. `pg_stat_statements` is reset around the timed runs and
   gives server execution time, buffers, I/O wait and WAL per call. One
   `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` is captured as JSON and as text.
4. Alternative fixes are applied, measured and rolled back one at a time.
5. Relation sizes are snapshotted, `fix.sql` runs, and sizes are snapshotted
   again. The difference is the fix's storage cost.
6. The statement is measured again. The result set is fingerprinted on both
   sides; if the fix changes the answer, the case fails.
7. `teardown.sql` restores the broken state, and `make run` replays the before
   and after statements under pgbench (8 clients, 30 s) for a concurrency view.

The N+1 case times a whole page render from the client, because the round
trips are the problem. The lock-contention case is a pgbench workload with
32 clients, reporting TPS, latency percentiles, and how many backends were
waiting on locks (sampled from `pg_stat_activity` every 50 ms).

## Honest limits

- The numbers come from a Docker VM on a laptop (8 vCPUs, 4 GB RAM, SSD), and
  the dataset is larger than the VM's memory. Absolute times will differ on
  your machine. The plan shapes, buffer counts and ratios are the reproducible
  part.
- Cache state isn't controlled beyond warm-up runs. A large scan right after
  a different case evicted its data can be several times slower than the same
  scan run twice in a row. That is real behaviour, but it means a single run's
  "before" numbers have noise.
- Case 07's payment call is a client-side `\sleep 2 ms` inside the
  transaction. That is what a synchronous external call looks like to the
  database, but it's a stand-in, not a real network call.
- `pg_stat_statements` has no CPU counter. The report's CPU column is execution
  time minus block I/O wait, which is exact for serial plans and understates
  CPU for parallel ones.
- Installing HypoPG from the PGDG repository moves the server to the newest
  16.x minor release, so the version string in the results is that release,
  not the base image tag.
