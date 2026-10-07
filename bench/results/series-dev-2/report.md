# Series evaluation — dev questions (task 0008 step 4)

- Questions: S1–S10 of `bench/series_eval.py` (written by the implementer; answer keys from the generator rows and
  checked against a hand-written reference QuerySpec). 1 run each, no format hint, plan cache off.
- Run `series-dev-1` (haiku only, 9/10) used an earlier converter; S8 there was a correct one-row answer read as a
  count. The converter now reads "one row shown with one measure" as that value. This run is on the final converter.

| id | what it tests | M-haiku | M-opus |
|---|---|---|---|
| S1 | period average | OK | OK |
| S2 | period sum | OK | OK |
| S3 | change over a period | OK | BAD¹ |
| S4 | sum per key | OK | OK |
| S5 | count with a value condition | OK | OK |
| S6 | below a 28-day moving average | OK | OK |
| S7 | month buckets | OK | OK |
| S8 | last value of a sparse measure | BAD² | OK |
| S9 | "last week" relative period | OK | OK |
| S10 | maximum in a quarter | OK | OK |
| | | **9/10** | **9/10** |

1. The change (0.9 kg) was right but came after `first`/`last`; the grader takes the first measure.
2. Listed the latest point, which has no body fat value (should skip points without it — `last` does).

Cost (API list price): M-haiku $0.13, M-opus $0.26. Raw rows: [results.jsonl](results.jsonl).
