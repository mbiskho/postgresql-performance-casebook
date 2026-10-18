# Leading-wildcard search

**Symptom.** Support agents search customers by part of a name. Every search
takes about a third of a second and uses three CPU cores, and support runs
thousands of them a day.

**What the plan shows.** A parallel sequential scan of all 2 million customers,
evaluating `full_name ILIKE '%sakura kusumawardani%'` on every row, followed by
a top-N sort of the few hundred matches.

**Why.** A B-tree orders strings from their first character. A pattern that
starts with `%` gives it no starting point, so no B-tree on `full_name` can
help. The HypoPG check in this case shows it: the planner ignores a
hypothetical B-tree index and keeps the sequential scan. Case-insensitive
matching makes it worse, because even a prefix search would need a
`lower()` expression index.

**Fix.** A trigram GIN index (`pg_trgm`, `gin_trgm_ops`). The index maps every
three-character substring to the rows that contain it. An `ILIKE '%term%'`
search becomes "rows that contain all of the term's trigrams", followed by a
recheck of those candidates. For a selective search term, that is a few
hundred rows instead of two million.

**Trade-offs.** Trigram indexes are big and slow to update. The size is
measured below, and every insert or name change updates many index entries.
Terms shorter than three characters have no trigrams, and very common
fragments (`%an%`) match a large fraction of rows, so both still end up
close to a full scan. If agents search by whole words, full-text search
(`tsvector` with a GIN index) is smaller and ranks results. Trigrams are for
fragments and typos.

**Catching it in production.** A `Seq Scan` with an `ILIKE`/`LIKE` filter that
starts with `%`, on a table that's otherwise accessed by index. Search
endpoints are typically low in `calls` and high in `total_exec_time`.
