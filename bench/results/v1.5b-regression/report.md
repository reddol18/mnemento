Model `haiku` via Claude Code CLI · run `v1.5b-regression` · frozen `e28a30ab46e80eaf` · git `00514623e2c7f63ec917b02408138b286920c603+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 27.3 / 69.7 | 25.7 / 68.1 | 0.93 | 5,927 (3,582) | 3,064 | 0.0272 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 6.1 / 11.7 | 4.1 / 9.5 | 0.96 | 7,272 (4,836) | 355 | 0.0266 | 0 |
