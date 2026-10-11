Model `haiku` via Claude Code CLI · run `low-issues-regression` · frozen `1f2559d842ba0b6e` · git `8f4345df36c0686b6fd87d17824d4e28248d97de+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 28/28 (100%) | — | 5.5 / 7.2 | 3.5 / 5.4 | 0.93 | 10,372 (8,027) | 667 | 0.0009 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 5.8 / 10.8 | 3.8 / 8.9 | 0.96 | 9,949 (7,514) | 349 | 0.0302 | 0 |
