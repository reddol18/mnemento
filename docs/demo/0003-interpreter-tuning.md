# Interpreter tuning (development questions, fictional demo data)

Generated 2026-10-03T01:01:29.599632+09:00 by `examples/tune_interpreter.py`. One run per cell — indicative, not a benchmark.

| config | Q | correct | input tok | output tok | thinking tok | cost USD | total s | model s |
|---|---|---|---|---|---|---|---|---|
| haiku / default thinking | Q2 | ✅ | 3834 | 697 | 531 | 0.007319 | 9.1 | 7.2 |
| haiku / default thinking | Q3 | ✅ | 3834 | 729 | 521 | 0.007479 | 9.2 | 7.3 |
| haiku / default thinking | Q4 | ✅ | 3854 | 6267 | 5633 | 0.035189 | 57.7 | 56.0 |
| haiku / thinking off | Q2 | ❌ | 3665 | 505 | 0 | 0.0152 | 7.4 | 5.5 |
| haiku / thinking off | Q3 | ❌ | 7457 | 323 | 0 | 0.009072 | 6.5 | 4.3 |
| haiku / thinking off | Q4 | ❌ | 3679 | 780 | 0 | 0.007579 | 9.5 | 7.6 |
| sonnet / thinking off | Q2 | ✅ | 2 | 314 | 90 | 0.020728 | 4.5 | 2.4 |
| sonnet / thinking off | Q3 | ✅ | 2 | 230 | 0 | 0.009563 | 4.9 | 2.8 |
| sonnet / thinking off | Q4 | ✅ | 2 | 385 | 0 | 0.011201 | 5.0 | 2.8 |
