Model `haiku` via Claude Code CLI · run `step4-regression` · frozen `81fc1c4921145e6d` · git `304e273741cb0c68a72cf17b2aeca7f258e26061+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 24.9 / 66.2 | 23.3 / 64.7 | 0.96 | 7,090 (4,654) | 2,514 | 0.0178 | 1 |
| 100 | M-opus | 28/28 (100%) | — | 6.2 / 8.7 | 4.3 / 6.9 | 0.93 | 8,126 (5,780) | 349 | 0.0270 | 0 |
