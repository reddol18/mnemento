Model `haiku` via Claude Code CLI · run `v1.1-regression` · frozen `c8dc7320c33fda3f` · git `93279252cf61e64fb252c71d789e9e58ab1cb5d2+dirty`

**v1.1 regression: M-haiku, eval set v2 data (all questions dev now), same format hint, 100 records, 1 repetition.**

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 18/20 (90%) | 8/9 (89%) | 22.5 / 48.5 | 20.9 / 46.8 | 0.93 | 4,915 (2,564) | 2,447 | 0.0221 |
