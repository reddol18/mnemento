Model `haiku` via Claude Code CLI · run `adr29-regression` · frozen `60a72abbc0f0d43f` · git `0aa11aa246ab89f9362fe0f165d1bf5c906cf5b3+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-claude-haiku-4-5-20251001 | 26/28 (93%) | — | 33.1 / 79.8 | 31.5 / 78.2 | 1.00 | 9,717 (7,191) | 3,929 | 0.0258 | 1 |
| 100 | M-opus | 28/28 (100%) | — | 6.7 / 15.6 | 4.8 / 12.1 | 1.00 | 11,672 (9,146) | 442 | 0.0335 | 0 |
| 100 | M-sonnet | 27/28 (96%) | — | 5.1 / 10.5 | 3.1 / 6.4 | 1.00 | 11,676 (9,150) | 344 | 0.0158 | 1 |
