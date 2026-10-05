Model `haiku` via Claude Code CLI · run `v1.9-regression` · frozen `fc85eaf777e61c64` · git `7bf484920bfba9e944434f1ffaa74ef2a40b1e52+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 25.0 / 99.6 | 23.3 / 97.8 | 0.96 | 6,195 (3,759) | 2,813 | 0.0264 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 6.0 / 11.5 | 4.1 / 7.7 | 1.00 | 7,593 (5,067) | 375 | 0.0292 | 0 |
