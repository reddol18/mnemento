Model `haiku` via Claude Code CLI · run `v1.4-regression` · frozen `915a88f325074f48` · git `a3ef66dee5e0220eb98f659d84577681202c4e08+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 27/28 (96%) | — | 21.3 / 54.1 | 19.7 / 52.5 | 0.93 | 4,937 (2,592) | 2,313 | 0.0214 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 5.6 / 8.0 | 3.6 / 6.2 | 0.96 | 5,984 (3,549) | 302 | 0.0254 | 0 |
