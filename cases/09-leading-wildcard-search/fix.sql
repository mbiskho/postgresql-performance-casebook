-- Trigram GIN index: ILIKE '%term%' becomes a lookup of the term's trigrams.
CREATE INDEX customers_full_name_trgm_idx
  ON customers USING gin (full_name gin_trgm_ops);
