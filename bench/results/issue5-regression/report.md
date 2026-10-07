Model `haiku` via Claude Code CLI · run `issue5-regression` · frozen `1cd627c1bb2a1aaf` · git `2808c029d5b8ea8dd9d7606ca9cad4f57c89ee03+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 24.1 / 89.0 | 22.6 / 87.4 | 0.93 | 7,139 (4,793) | 2,481 | 0.0176 | 1 |
| 100 | M-opus | 28/28 (100%) | — | 5.6 / 9.1 | 3.9 / 7.4 | 0.93 | 8,125 (5,780) | 350 | 0.0270 | 0 |
