# Series evaluation — unseen price questions (task 0008 step 5)

- Questions Q1–Q5 of `bench/series_eval.py`, written by the directing session without seeing the code (ids renamed from
  P1–P5 because dev uses those). Approved by the user; run once each on code 0a92ee1, no format hint, plan cache off.
- Grading as fixed before the run: strict `ok` first; `ok:candidate` when the right number is another returned value
  (rule added before this run, from the dev results).

| id | what it tests | M-haiku | M-opus |
|---|---|---|---|
| Q1 | as-of a past date + account filter + a holding without prices | OK | OK |
| Q2 | stale price + a ratio | OK | OK |
| Q3 | as-of the record's own date (trade day) vs today | OK | ok:candidate¹ |
| Q4 | holdings whose price is more than a week old (not expressible) | BAD² | BAD³ |
| Q5 | as-of a past date + account filter | OK | ok:candidate¹ |
| **strict** | | **4/5** | **2/5** |
| with candidate credit | | 4/5 | 4/5 |

1. Right number among the returned values (Q3: change listed before its percentage; Q5: P/L after the totals).
2. Asked back: filtering on the age of the as-of point cannot be expressed.
3. Found it with a series aggregate (no point in the last 7 days → sec_900002), but the set grader reads listed records
   only, not aggregate groups. Recorded as BAD; a grader change would apply from the next measurement only.

Status: these 5 questions are used up by this measurement.
Cost (API list price): M-haiku $0.25, M-opus $0.23. Raw rows: [results.jsonl](results.jsonl).
