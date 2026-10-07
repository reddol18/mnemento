# Ingest evaluation — dev, first run (task 0008 step 6)

Code 315d045, fictional dev set (`bench/ingest_sets/dev`, 2 files, 13 chunks), preview only, 1 run per model.
The raw rows were lost (the worktree holding them was removed before they were copied); the score lines below are the
runner's printed output, copied at the time.

| metric | M-haiku | M-opus |
|---|---|---|
| classification | 8/13 | 13/13 |
| field precision | 16/21 | 16/21 |
| field recall | 16/16 | 16/16 |
| new-type values | 0/12 | 12/12 |
| new-type kind | 0/2 | 1/2 |
| values not in the source | 0 | 0 |
| wrong merges | 1* | 1* |
| siblings | 1/1 | 1/1 |
| conflict recall | 1/1 | 1/1 |
| cost (API list price) | $0.067 | $0.142 |

\* Scorer bug, fixed in c964756: a trade and its decision share a line and were counted as two facts in one record.

Findings that led to the fixes in c964756: both models extracted a `security` record for every security named in a
note (the precision loss); haiku called the weight table and the reading notes not data; opus made the weight table an
entity (a series needed a key).
