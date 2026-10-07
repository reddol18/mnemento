# Ingest evaluation — unseen analysis-heavy notes (issue #15)

- Set: `bench/ingest_sets/unseen_analysis` — two fictional files (16 chunks) and labels written by the directing
  session without seeing the code: analysis, a comparison, a retrospective and plans (not data, numbers included);
  two one-off dated facts (must not become types); a reading log split over two files under different headings
  (must become one type); a dining log of three entries. Approved by the user; one preview per model on code 4655ca6,
  extended thinking off, cost cap $1 per model. The set is now used up.

| metric | M-haiku | M-opus |
|---|---|---|
| new types proposed (expected 2) | 2 | 2 |
| one-off facts that became a type | 0 | 0 |
| classification | 16/16 | 16/16 |
| new-type values | 24/24 | 24/24 |
| new-type kind | 2/2 | 2/2 |
| values not in the source | 0 | 0 |
| ambiguous (the two one-offs) | 2 | 2 |
| estimate with headroom / actual cost | $0.26 / $0.029 | $0.74 / $0.115 |

Compare the real memory-folder preview before these changes: 68 new types for 1,615 chunks, most backed by 1–3
records, at about 2× the estimate.
