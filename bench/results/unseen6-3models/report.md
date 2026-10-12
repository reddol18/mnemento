Model `haiku` via Claude Code CLI · run `unseen6-3models` · frozen `1de5170749735678` · git `3c90c8a453fd6f91b4b5c7cbd33bb2d3834581ef+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-claude-haiku-4-5-20251001 | — | — | 30.0 / 70.9 | 27.7 / 67.4 | 1.33 | 12,964 (9,596) | 3,360 | 0.0244 | 2 |
| 100 | M-opus | — | — | 6.1 / 7.7 | 4.1 / 5.7 | 1.00 | 11,662 (9,136) | 396 | 0.0300 | 2 |
| 100 | M-sonnet | — | — | 5.7 / 10.4 | 3.2 / 6.0 | 1.22 | 14,271 (11,184) | 341 | 0.0170 | 2 |
