Model `haiku` via Claude Code CLI · run `step5-regression` · frozen `e89d3f12d5d5f81d` · git `1369dd6f862afe61d30372ab5dbea191589d0aa6+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 25/28 (89%) | — | 23.8 / 85.3 | 22.2 / 83.4 | 0.93 | 7,590 (5,245) | 2,510 | 0.0181 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 5.9 / 10.2 | 3.9 / 8.4 | 0.96 | 9,405 (6,969) | 343 | 0.0278 | 0 |
