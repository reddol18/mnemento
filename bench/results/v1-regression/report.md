Model `haiku` via Claude Code CLI · run `v1-regression` · frozen `59a900c40484eb4b` · git `e925f2c0ad2855cc371d42a9e22dd3bd518a5de2+dirty`

**v1 regression: M only, v0 evaluation set and condition, 12 questions, 1 repetition.**

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 100 | M Mnemento (template answer) | 4/4 (100%) | 6/8 (75%) | 18.5 / 38.3 | 16.9 / 36.7 | 0.92 | 4,325 (2,009) | 1,935 | 0.0183 |
| 1,000 | M Mnemento (template answer) | 3/4 (75%) | 8/8 (100%) | 16.1 / 39.3 | 14.5 / 37.7 | 0.92 | 4,325 (2,010) | 1,706 | 0.0172 |
