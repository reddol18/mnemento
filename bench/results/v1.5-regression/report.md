Model `haiku` via Claude Code CLI · run `v1.5-regression` · frozen `3682232c54e49e8f` · git `f439963ceb6178002215189701ed243dbc4ef2cd+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 27/28 (96%) | — | 20.6 / 46.5 | 18.8 / 44.8 | 0.96 | 6,156 (3,720) | 2,251 | 0.0236 | 0 |
| 100 | M-opus | 27/28 (96%) | — | 5.9 / 9.4 | 4.0 / 6.5 | 0.96 | 7,272 (4,836) | 357 | 0.0280 | 0 |
