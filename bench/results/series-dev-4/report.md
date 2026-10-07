# Series evaluation — price dev questions (task 0008 step 5)

- P1–P4 of `bench/series_eval.py`: fictional securities, daily closes, holdings and trades (`generate_market`).
- `series-dev-3` (code 1369dd6): S1–S10 as in step 4 (M-haiku 9/10, M-opus 9/10, S3 is the known grader rule) and
  P1–P4 M-haiku 2/4, M-opus 2/4. The misses led to three general fixes (26d70f0): name filters on a series key that
  references records, records that point at a mentioned series' key type shown to the interpreter, unused computed
  values rejected.
- `series-dev-4` (code bc585b9), P1–P4 only:

| id | question | M-haiku | M-opus |
|---|---|---|---|
| P1 | 8월 평균 종가 | OK | OK |
| P2 | 보유 종목별 평가 손익 | BAD¹ | OK |
| P3 | 평가 손익 합계 | OK | BAD² |
| P4 | 산 날 종가 | OK | OK |
| | | **3/4** | **3/4** |

1. Right values per security (15,120 / −20,500) as the fourth measure; the grader read the first (units).
2. Right P/L split by account (−20,500 / +15,120); the grader wanted one number (−5,380).

Both are grader-rule misses with the right numbers in the answer. From the next measurement (unseen price questions)
the grader also gives a separate `ok:candidate` when the right number is another returned value (any measure of a single
group, or a sum/count measure totalled over groups); strict scores stay as before and are reported first.

Cost (API list price): series-dev-3 about $0.75, series-dev-4 about $0.30.
