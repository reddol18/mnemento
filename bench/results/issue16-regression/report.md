Model `haiku` via Claude Code CLI · run `issue16-regression` · frozen `7db5de10f3c67ca0` · git `2f2f6c8ecab4180f3a40c3766160f1af62f1b805+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-claude-haiku-4-5-20251001 | 27/28 (96%) | — | 24.7 / 71.4 | 23.1 / 69.9 | 1.00 | 8,823 (6,297) | 2,882 | 0.0200 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 5.9 / 11.4 | 3.9 / 9.6 | 1.00 | 10,580 (8,054) | 378 | 0.0317 | 0 |
