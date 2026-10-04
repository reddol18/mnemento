Model `haiku` via Claude Code CLI · run `v1.6-regression` · frozen `8024e4f8c975ac48` · git `341be5e44c9e97a6f4b26d9aa0ad2200bb2b915a+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 27/28 (96%) | — | 30.3 / 93.2 | 28.5 / 89.9 | 1.04 | 6,626 (4,009) | 3,342 | 0.0299 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 6.2 / 9.3 | 4.3 / 6.5 | 0.96 | 7,272 (4,836) | 375 | 0.0270 | 0 |
