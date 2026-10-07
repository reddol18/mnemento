# Series evaluation — unseen questions, first measurement (task 0008 step 4)

- Questions: U1–U6, U8 of `bench/series_eval.py`, written by the directing session without seeing the code (U7 was
  dropped before measurement by the agreed rule: 1st and 2nd differ by 0.007 kg). No system was run on them before
  this measurement. 1 run each, no format hint (the question text only), plan cache off.
- Data: fictional weight / spending series (`bench/series_gen.py`, seed 20261005), now = 2026-09-27 21:00 KST.
- Grading is mechanical (`to_answer` + `grade`, fixed before this run): a single-group answer's number is its
  **first** measure.

| id | what it tests | M-haiku | M-opus |
|---|---|---|---|
| U1 | period average | OK | OK |
| U2 | first → last change | BAD¹ | BAD¹ |
| U3 | quarter-to-date sum | OK | OK |
| U4 | month bucket + argmax | OK | OK |
| U5 | two periods in one number (not expressible in one QuerySpec) | BAD² | BAD² |
| U6 | sparse field average | OK | OK |
| U8 | days without a point (not expressible) | BAD³ | BAD³ |
| **strict** | | **4/7** | **4/7** |
| value present in the answer (secondary) | | 5/7 | 5/7 |

1. Both computed the right change (−0.1 kg) but listed it after `first` and `last`; the strict rule takes the first
   measure (58.6). Counted in the secondary line.
2. haiku grouped the 8 weeks by week and left the subtraction to the reader; opus computed both 4-week sums
   (45 and 50) with per-measure `where` but not their difference (−5). Not counted as present.
3. Both counted rows with amount 0 (there are none: a day without spending has no point). The right numbers
   (식비 2, 교통 7, 카페 12) appear only in the gap warning, which the grader does not read. Not counted as present.

Cost (API list price): M-haiku $0.15, M-opus $0.15. Raw rows: [results.jsonl](results.jsonl).
