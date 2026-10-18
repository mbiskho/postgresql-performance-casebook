-- Spread the sale's stock over 16 slots. A checkout picks a slot at random,
-- so concurrent buyers usually lock different rows. Reading total stock is
-- a sum over the slots. When a slot runs dry the app retries another slot;
-- with 16 slots the last few units need that fallback, which is the price of
-- the extra concurrency.
INSERT INTO flash_sale_stock (sale_id, slot, remaining)
SELECT 1, s, 0 FROM generate_series(1, 15) AS s;

UPDATE flash_sale_stock AS f
SET remaining = t.total / 16 + CASE WHEN f.slot = 0 THEN mod(t.total, 16) ELSE 0 END
FROM (SELECT sum(remaining) AS total FROM flash_sale_stock WHERE sale_id = 1) AS t
WHERE f.sale_id = 1;

-- The second half of the fix is in after.pgbench: the payment call moves out
-- of the transaction and the hot UPDATE moves to just before COMMIT.
