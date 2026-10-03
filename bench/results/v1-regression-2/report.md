Model `haiku` via Claude Code CLI · run `v1-regression-2` · frozen `c1505c45f0c48366` · git `5ca9882b8a6cf358c8440a7699d631327ecdfbe9+dirty`

**v1 regression: M only, v0 evaluation set and condition, 12 questions, 1 repetition.**

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 100 | M Mnemento (template answer) | 4/4 (100%) | 7/8 (88%) | 12.9 / 25.6 | 11.3 / 23.9 | 0.92 | 4,325 (2,009) | 1,314 | 0.0152 |
| 1,000 | M Mnemento (template answer) | 3/4 (75%) | 8/8 (100%) | 13.8 / 29.0 | 12.3 / 27.4 | 0.83 | 3,929 (1,824) | 1,435 | 0.0150 |
