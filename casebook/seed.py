"""Deterministic dataset generator.

At scale 1.0 this produces about 64 million rows:

    customers      2,000,000
    products         100,000
    orders        15,000,000
    order_items  ~37,400,000   (geometric, mean ~2.5 per order)
    page_views    10,000,000   (hash partitioned, 16 ways)

Rows are generated server-side with INSERT ... SELECT so the data never crosses
the wire. Python decides the distributions and the chunking; each chunk calls
setseed() with a seed derived from its position, so the same scale always
produces the same data no matter how many workers load it.

Shapes that matter for the cases:

* Customer activity is a power law: a few accounts have tens of thousands of
  orders, the median customer has a handful, ~3% have none, ~3% of orders are
  guest checkouts with NULL customer_id.
* Orders arrive with growth (more recent months are busier) and are appended
  in time order, so created_at is physically correlated with the heap.
* Order status depends on age: old orders are terminal, recent ones are still
  in flight. That is a cross-column correlation the planner does not know.
* country -> currency, locale and city -> timezone are functional
  dependencies, as in any real customer table.
* Product and page popularity are Zipf-like, and product popularity is
  scattered across ids instead of concentrated on the low ones.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import refdata
from .db import ROOT, connect, scalar, split_sections, statements

AS_OF = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
SEED_VERSION = "3"

BASE_COUNTS = {
    "customers": 2_000_000,
    "products": 100_000,
    "orders": 15_000_000,
    "page_views": 10_000_000,
}
CHUNK = 500_000

# A prime larger than any table here. Multiplying a popularity rank by it
# modulo n scatters popular ids across the key space (gcd(p, n) = 1).
SCATTER_PRIME = 2_147_483_647


def counts_for(scale: float) -> dict[str, int]:
    return {k: max(int(v * scale), 1_000) for k, v in BASE_COUNTS.items()}


def _arrays() -> dict[str, object]:
    countries = refdata.COUNTRIES
    country_slots = refdata.slots([(i, w) for i, (_, w, *_rest) in enumerate(countries)])
    city_flat: list[str] = []
    tz_flat: list[str] = []
    for _, _, _, _, cities in countries:
        city_slots = refdata.slots([((c, tz), w) for c, w, tz in cities])
        city_flat.extend(c for c, _ in city_slots)
        tz_flat.extend(tz for _, tz in city_slots)
    paths = refdata.page_paths()
    return {
        "c_idx": country_slots,  # slot -> 0-based country index
        "c_code": [c[0] for c in countries],
        "c_currency": [c[2] for c in countries],
        "c_locale": [c[3] for c in countries],
        "city": city_flat,
        "tz": tz_flat,
        "first": refdata.FIRST_NAMES,
        "last": refdata.LAST_NAMES,
        "domain": refdata.slots(refdata.EMAIL_DOMAINS),
        "tier": refdata.slots(refdata.TIERS),
        "category": refdata.slots(refdata.CATEGORIES),
        "brand": refdata.BRANDS,
        "adj": refdata.ADJECTIVES,
        "noun": refdata.NOUNS,
        "paths": paths,
        "referrer": refdata.slots(refdata.REFERRERS),
    }


CUSTOMERS_SQL = """
INSERT INTO customers (id, email, full_name, country, city, currency, timezone, locale, tier, signup_at)
SELECT g,
       lower(fn) || '.' || lower(ln) || g || '@' || (%(domain)s::text[])[1 + floor(r_dom * 1000)::int],
       fn || ' ' || ln,
       (%(c_code)s::text[])[ci + 1],
       (%(city)s::text[])[ci * 1000 + ys],
       (%(c_currency)s::text[])[ci + 1],
       (%(tz)s::text[])[ci * 1000 + ys],
       (%(c_locale)s::text[])[ci + 1],
       (%(tier)s::text[])[1 + floor(r_tier * 1000)::int],
       %(as_of)s::timestamptz - interval '1826 days' * (1 - sqrt((g - r_t) / %(n)s::float8))
FROM (
  SELECT g,
         (%(c_idx)s::int[])[1 + floor(random() * 1000)::int] AS ci,
         1 + floor(random() * 1000)::int AS ys,
         (%(first)s::text[])[1 + floor(random() * %(n_first)s)::int] AS fn,
         (%(last)s::text[])[1 + floor(%(n_last)s * power(random(), 1.6))::int] AS ln,
         random() AS r_dom, random() AS r_tier, random() AS r_t
  FROM generate_series(%(lo)s::bigint, %(hi)s::bigint) g
) r
"""

PRODUCTS_SQL = """
INSERT INTO products (id, sku, name, category, brand, price_cents, created_at)
SELECT g,
       'SKU-' || lpad(g::text, 8, '0'),
       b || ' ' || a || ' ' || nn || ' ' || (1 + mod(g, 97)),
       cat, b,
       greatest(99, round(exp(7.8 + 1.0 * z)))::int,
       %(as_of)s::timestamptz - interval '1460 days' * (1 - (g - random()) / %(n)s::float8)
FROM (
  SELECT g,
         (%(category)s::text[])[1 + floor(random() * 1000)::int] AS cat,
         (%(brand)s::text[])[1 + floor(%(n_brand)s * power(random(), 1.8))::int] AS b,
         (%(adj)s::text[])[1 + floor(random() * %(n_adj)s)::int] AS a,
         (%(noun)s::text[])[1 + floor(random() * %(n_noun)s)::int] AS nn,
         sqrt(-2 * ln(1 - random())) * cos(2 * pi() * random()) AS z
  FROM generate_series(%(lo)s::bigint, %(hi)s::bigint) g
) r
"""

ORDERS_SQL = """
INSERT INTO orders (id, customer_id, status, total_cents, created_at, updated_at)
SELECT g, cust,
       CASE WHEN %(as_of)s::timestamptz - created_at > interval '14 days' THEN
              CASE WHEN us < 0.92 THEN 'delivered' WHEN us < 0.97 THEN 'cancelled' ELSE 'refunded' END
            ELSE
              CASE WHEN us < 0.12 THEN 'pending' WHEN us < 0.35 THEN 'paid'
                   WHEN us < 0.80 THEN 'shipped' ELSE 'delivered' END
       END,
       greatest(100, round(exp(7.9 + 0.9 * z)))::bigint,
       created_at,
       created_at + interval '1 hour' * (1 + 200 * ua)
FROM (
  SELECT g,
         CASE WHEN random() < 0.03 THEN NULL
              ELSE 1 + floor(%(n_cust)s * power(random(), 2.2))::bigint END AS cust,
         %(start)s::timestamptz + interval '1095 days' * sqrt((g - random()) / %(n)s::float8) AS created_at,
         random() AS us, random() AS ua,
         sqrt(-2 * ln(1 - random())) * cos(2 * pi() * random()) AS z
  FROM generate_series(%(lo)s::bigint, %(hi)s::bigint) g
) r
"""

# Items per order: 1 + floor(Exp(mean 1.95)), capped at 12 -> mean ~2.5.
# id = order_id * 16 + line, so ids are unique without a shared sequence and
# items stay physically next to their order.
ORDER_ITEMS_SQL = """
INSERT INTO order_items (id, order_id, product_id, quantity, unit_price_cents)
SELECT o.id * 16 + k, o.id,
       1 + mod(floor(%(n_prod)s * power(random(), 2.5))::bigint * %(scatter)s, %(n_prod)s),
       CASE WHEN random() < 0.85 THEN 1 ELSE 2 + floor(random() * 3)::int END,
       greatest(99, round(exp(7.6 + 0.9 * sqrt(-2 * ln(1 - random())) * cos(2 * pi() * random()))))::int
FROM generate_series(%(lo)s::bigint, %(hi)s::bigint) AS o(id)
CROSS JOIN LATERAL generate_series(
  1, least(12, 1 + floor(-ln(1 - random()) * 1.95)::int + (o.id * 0)::int)) AS k
"""

PAGE_VIEWS_SQL = """
INSERT INTO page_views (user_id, viewed_at, path, referrer, duration_ms)
SELECT 1 + mod(floor(%(n_cust)s * power(random(), 2.5))::bigint * %(scatter)s, %(n_cust)s),
       %(pv_start)s::timestamptz + interval '540 days' * sqrt((g - random()) / %(n)s::float8),
       (%(paths)s::text[])[1 + floor(%(n_paths)s * power(random(), 3))::int],
       (%(referrer)s::text[])[1 + floor(random() * 1000)::int],
       greatest(200, round(exp(9.5 + 1.1 * sqrt(-2 * ln(1 - random())) * cos(2 * pi() * random()))))::int
FROM generate_series(%(lo)s::bigint, %(hi)s::bigint) g
"""


def _load(table: str, sql: str, n: int, params: dict, workers: int, seed_base: int) -> None:
    chunks = [(lo, min(lo + CHUNK - 1, n)) for lo in range(1, n + 1, CHUNK)]
    started = time.monotonic()
    done = 0

    def work(idx_chunk):
        idx, (lo, hi) = idx_chunk
        with connect() as conn:
            conn.execute("SET synchronous_commit = off")
            # Deterministic per chunk: setseed takes a value in [-1, 1].
            conn.execute("SELECT setseed(%s)", (((seed_base * 7919 + idx) % 20000) / 10000.0 - 1,))
            conn.execute(sql, {**params, "lo": lo, "hi": hi, "n": n})
        return hi - lo + 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(work, enumerate(chunks)):
            done += rows
            elapsed = time.monotonic() - started
            print(f"  {table:<12} {done:>12,} / {n:,} source rows  ({elapsed:6.1f}s)", flush=True)


def _parallel(sqls: list[str], workers: int) -> None:
    def work(sql):
        t = time.monotonic()
        with connect() as conn:
            conn.execute("SET maintenance_work_mem = '512MB'")
            conn.execute(sql)
        first = sql.strip().splitlines()[0]
        print(f"  {time.monotonic() - t:6.1f}s  {first[:90]}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, sqls))


def drop_all(conn) -> None:
    conn.execute("""
        DROP TABLE IF EXISTS page_views, page_views_by_month, order_items, orders,
                             products, customers, casebook_meta, shipment_events,
                             flash_sale_stock, flash_sale_orders, daily_order_stats CASCADE
    """)


def is_seeded(conn) -> dict | None:
    if not scalar(conn, "SELECT to_regclass('casebook_meta') IS NOT NULL"):
        return None
    rows = dict(conn.execute("SELECT key, value FROM casebook_meta").fetchall())
    return rows if rows.get("seed_complete") == "true" else None


def run(scale: float, workers: int = 4, reset: bool = False) -> None:
    counts = counts_for(scale)
    schema = split_sections(ROOT / "sql" / "schema.sql")

    with connect() as conn:
        existing = is_seeded(conn)
        if existing and not reset:
            if existing.get("scale") == str(scale) and existing.get("seed_version") == SEED_VERSION:
                print(f"Already seeded at scale {scale} (version {SEED_VERSION}); use --reset to rebuild.")
                return
            raise SystemExit(
                f"Database holds a seed at scale {existing.get('scale')} version "
                f"{existing.get('seed_version')}; rerun with --reset to replace it."
            )
        print("Creating schema")
        drop_all(conn)
        for stmt in statements(schema["tables"]):
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO casebook_meta VALUES ('as_of', %s), ('scale', %s), ('seed_version', %s)",
            (AS_OF.isoformat(), str(scale), SEED_VERSION),
        )

    arrays = _arrays()
    params = {
        **arrays,
        "as_of": AS_OF,
        "start": AS_OF - timedelta(days=1095),
        "pv_start": AS_OF - timedelta(days=540),
        "n_first": len(arrays["first"]),
        "n_last": len(arrays["last"]),
        "n_brand": len(arrays["brand"]),
        "n_adj": len(arrays["adj"]),
        "n_noun": len(arrays["noun"]),
        "n_paths": len(arrays["paths"]),
        "n_cust": counts["customers"],
        "n_prod": counts["products"],
        "scatter": SCATTER_PRIME,
    }

    t0 = time.monotonic()
    print(f"Loading data at scale {scale}")
    _load("customers", CUSTOMERS_SQL, counts["customers"], params, workers, 1)
    _load("products", PRODUCTS_SQL, counts["products"], params, workers, 2)
    # Orders load on one worker so the heap is strictly append-ordered by
    # created_at, as a real orders table is. Items can load in parallel: each
    # chunk still writes an order's lines next to each other.
    _load("orders", ORDERS_SQL, counts["orders"], params, 1, 3)
    _load("order_items", ORDER_ITEMS_SQL, counts["orders"], params, workers, 4)
    # Same for clickstream: append-ordered by viewed_at within each partition.
    _load("page_views", PAGE_VIEWS_SQL, counts["page_views"], params, 1, 5)

    print("Building indexes")
    _parallel(statements(schema["indexes"]), workers=3)

    print("VACUUM ANALYZE")
    _parallel([f"VACUUM (ANALYZE) {t}" for t in
               ("customers", "products", "orders", "order_items", "page_views")], workers=3)

    with connect() as conn:
        conn.execute("CHECKPOINT")
        totals = {t: scalar(conn, f"SELECT count(*) FROM {t}") for t in
                  ("customers", "products", "orders", "order_items", "page_views")}
        for t, c in totals.items():
            conn.execute("INSERT INTO casebook_meta VALUES (%s, %s)", (f"rows.{t}", str(c)))
        conn.execute("INSERT INTO casebook_meta VALUES ('seed_seconds', %s), ('seed_complete', 'true')",
                     (f"{time.monotonic() - t0:.0f}",))
        size = scalar(conn, "SELECT pg_size_pretty(pg_database_size(current_database()))")

    print()
    for t, c in totals.items():
        print(f"  {t:<12} {c:>12,}")
    print(f"  {'total':<12} {sum(totals.values()):>12,} rows, database {size}, "
          f"{time.monotonic() - t0:.0f}s")
