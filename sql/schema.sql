-- Base schema for the casebook, as the imaginary product shipped it.
--
-- The indexes below are the ones a team would plausibly have created on day
-- one: primary keys, a unique email, a single-column foreign-key index and a
-- created_at index. Each case's setup.sql adds whatever extra "broken" state it
-- needs, and each fix.sql changes it. Nothing here is pre-tuned.
--
-- Tables are created without indexes by the seeder and the indexes are built
-- after the bulk load (see casebook/seed.py); this file is the single source of
-- truth for both.

-- @tables
CREATE TABLE IF NOT EXISTS casebook_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id         bigint      NOT NULL,
    email      text        NOT NULL,
    full_name  text        NOT NULL,
    country    text        NOT NULL,  -- ISO 3166 alpha-2
    city       text        NOT NULL,
    currency   text        NOT NULL,  -- determined by country
    timezone   text        NOT NULL,  -- determined by city
    locale     text        NOT NULL,  -- determined by country
    tier       text        NOT NULL,
    signup_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id          bigint      NOT NULL,
    sku         text        NOT NULL,
    name        text        NOT NULL,
    category    text        NOT NULL,
    brand       text        NOT NULL,
    price_cents integer     NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id          bigint      NOT NULL,
    customer_id bigint,               -- NULL for guest checkout
    status      text        NOT NULL,
    total_cents bigint      NOT NULL,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id               bigint  NOT NULL,
    order_id         bigint  NOT NULL,
    product_id       bigint  NOT NULL,
    quantity         integer NOT NULL,
    unit_price_cents integer NOT NULL
);

-- Clickstream, partitioned by HASH(user_id) because the first consumer was a
-- per-user activity feed. Case 04 is about what that choice costs everyone
-- else.
CREATE TABLE IF NOT EXISTS page_views (
    user_id     bigint      NOT NULL,
    viewed_at   timestamptz NOT NULL,
    path        text        NOT NULL,
    referrer    text,
    duration_ms integer     NOT NULL
) PARTITION BY HASH (user_id);

DO $$
BEGIN
  FOR i IN 0..15 LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS page_views_h%s PARTITION OF page_views
         FOR VALUES WITH (MODULUS 16, REMAINDER %s)', lpad(i::text, 2, '0'), i);
  END LOOP;
END $$;

-- @indexes
ALTER TABLE customers   ADD CONSTRAINT customers_pkey   PRIMARY KEY (id);
ALTER TABLE products    ADD CONSTRAINT products_pkey    PRIMARY KEY (id);
ALTER TABLE orders      ADD CONSTRAINT orders_pkey      PRIMARY KEY (id);
ALTER TABLE order_items ADD CONSTRAINT order_items_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX customers_email_key     ON customers (email);
CREATE UNIQUE INDEX products_sku_key        ON products (sku);
CREATE INDEX orders_customer_id_idx         ON orders (customer_id);
CREATE INDEX orders_created_at_idx          ON orders (created_at);
CREATE INDEX order_items_order_id_idx       ON order_items (order_id);
CREATE INDEX page_views_user_viewed_idx     ON page_views (user_id, viewed_at);
