Model `haiku` via Claude Code CLI · run `adr30-e12-3models` · frozen `2b354d5721ff00fc` · git `10edf7062f29df9d29f90f8a0696d298244365a3+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-claude-haiku-4-5-20251001 | 6/6 (100%) | — | 17.5 / 31.0 | 15.5 / 29.3 | 1.00 | 9,843 (7,317) | 1,811 | 0.0171 | 0 |
| 100 | M-opus | 6/6 (100%) | — | 6.4 / 7.2 | 4.4 / 5.4 | 1.00 | 11,793 (9,267) | 388 | 0.0307 | 0 |
| 100 | M-sonnet | 6/6 (100%) | — | 4.4 / 4.6 | 2.3 / 2.5 | 1.00 | 11,798 (9,272) | 257 | 0.0081 | 0 |
