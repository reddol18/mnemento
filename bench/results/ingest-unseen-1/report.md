# Ingest evaluation — unseen notes (task 0008 step 6)

- Set: `bench/ingest_sets/unseen` — three fictional memory-style files (25 chunks) and labels written by the directing
  session without seeing the code; aligned with the splitter and ADR-0017 before any run (see `label_notes`). Approved
  by the user; one preview per model on code 3badc59 (never applied). These notes are now used up.

| metric | M-haiku | M-opus |
|---|---|---|
| classification (data / not_data) | 25/25 | 25/25 |
| field precision (existing types) | 24/24 | 23/23 |
| field recall (existing types) | 24/24 | 23/24¹ |
| new-type values | 20/20 | 17/20² |
| new-type kind | 1/2³ | 1/2³ |
| **values not in the source** | **0** | **0** |
| dropped values | 0 | 0 |
| wrong merges | 0 | 0 |
| siblings | 1/1 | 1/1 |
| conflict recall | 1/1 | 1/1 |
| ambiguous (expected: the unknown security, twice) | 2 | 2 |
| cost (API list price) | $0.11 | $0.18 |

1. The decision whose chunk gives only the reason: opus left the date out (haiku took it from the context).
2. opus stored the workout kinds as enum values (running/walking) with the note's words (러닝/걷기) as labels — the
   evidence check accepts a value through its labels; the scorer compares with the words in the note.
3. Both made the workout log a series keyed by activity; the labels say entity. The sleep table is a series (key-less)
   in both, as labelled.
