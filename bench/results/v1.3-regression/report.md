Model `haiku` via Claude Code CLI · run `v1.3-regression` · frozen `559476b2ccabf9b0` · git `3853828723adcd58069506f0032687931128dd5b+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 25/28 (89%) | — | 18.7 / 43.1 | 17.1 / 41.5 | 0.93 | 4,937 (2,592) | 2,042 | 0.0201 | 1 |
| 100 | M-opus | 28/28 (100%) | — | 5.5 / 7.9 | 3.6 / 5.7 | 0.96 | 5,985 (3,549) | 309 | 0.0256 | 0 |
