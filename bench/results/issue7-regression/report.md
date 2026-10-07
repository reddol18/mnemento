Model `haiku` via Claude Code CLI · run `issue7-regression` · frozen `81e94513bc2ad536` · git `3badc59ebd79c824cd4ab66fa61eda8d47af7ba5+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 26.2 / 57.0 | 24.6 / 55.5 | 0.96 | 8,093 (5,657) | 2,686 | 0.0192 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 5.9 / 11.1 | 4.0 / 9.3 | 0.96 | 9,657 (7,222) | 353 | 0.0302 | 0 |
