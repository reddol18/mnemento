Model `haiku` via Claude Code CLI · run `unseen4-haiku45` · frozen `235b4991a81b7ced` · git `ddc99e70e8c68a84cee4820bc0d3ad87acc7bce7+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-claude-haiku-4-5-20251001 | — | — | 52.3 / 220.6 | 50.4 / 217.1 | 1.14 | 9,847 (6,960) | 5,782 | 0.0353 | 0 |

## Unseen v4 (issue #13) — results

Questions X1–X8 of `docs/eval/unseen-v4.md` (written by the directing session without seeing the code; X6 dropped
before measurement because the generator has no postings). Claude Haiku 4.5 pinned, as in every v3 measurement; one
run on code ddc99e7, same format hint. Approved by the user; cost $0.25 (API list price). These questions are used up.

| id | targets | result |
|---|---|---|
| X1 | dictionary default "quickly viewed" | OK |
| X2 | condition on one measure only (overall vs quickly-viewed pass rate) | OK |
| X3 | elapsed to today (> 2 weeks, not viewed) | OK |
| X4 | per-platform mean days to view | BAD¹ |
| X5 | condition on one measure only (January count vs viewed within 7 days) | OK |
| X7 | vague word chosen by the model ("오래 묵혀둔", stated as 30 days) | OK |
| X8 | mean days until the result | BAD² |
| **total** | | **5/7** |

1. The reading was right (per platform, mean days between the dates — the accepted alternative), but the answer
   converter takes the first measure (the count) as the value. Recorded as BAD; a converter change would apply to the
   next measurement only.
2. Averaged hours to "passed" and to "rejected" separately and reported hours, not one mean in days — the same
   hours/days unit weakness as dev D5 with Haiku 4.5.

For comparison: in the v3 measurement the elapsed-condition question W5 could not be expressed at all; here every
elapsed and per-measure question (X1–X3, X5) passed with the same model.
