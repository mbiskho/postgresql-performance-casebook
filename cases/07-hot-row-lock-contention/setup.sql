-- One flash sale, one stock row. The remaining count is large so the sale
-- never sells out during a benchmark run.
DROP TABLE IF EXISTS flash_sale_orders;
DROP TABLE IF EXISTS flash_sale_stock;

CREATE TABLE flash_sale_stock (
    sale_id   integer NOT NULL,
    slot      integer NOT NULL,
    remaining bigint  NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (sale_id, slot)
);
INSERT INTO flash_sale_stock VALUES (1, 0, 1000000000);

CREATE TABLE flash_sale_orders (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sale_id     integer     NOT NULL,
    customer_id bigint      NOT NULL,
    slot        integer     NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
