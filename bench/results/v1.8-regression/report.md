Model `haiku` via Claude Code CLI · run `v1.8-regression` · frozen `245233b797a2ec1c` · git `1e059f8a19bb1dbe273b47781b5aa56a9b98cdb9+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 23.6 / 59.6 | 21.5 / 57.7 | 0.96 | 6,199 (3,763) | 2,594 | 0.0254 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 6.3 / 10.3 | 3.9 / 6.4 | 0.96 | 7,323 (4,887) | 358 | 0.0281 | 0 |
