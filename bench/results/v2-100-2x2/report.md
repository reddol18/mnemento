Model `haiku` via Claude Code CLI · run `v2-100-2x2` · frozen `b4c05de5f3fff9cf` · git `832de046718313c1d25dde9ab9074aa5a40cc37b+dirty`

**Eval set v2 at 100 records: M and B1 each on haiku and opus (2x2), dev 20 + unseen 9, same format hint, 3 repetitions.**

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 100 | B1-haiku | 36/60 (60%) | 18/27 (67%) | 44.8 / 124.3 | 42.8 / 122.0 | 1.00 | 59,264 (56,738) | 5,738 | 0.0508 |
| 100 | B1-opus | 56/60 (93%) | 27/27 (100%) | 15.9 / 27.7 | 13.9 / 25.9 | 1.00 | 34,573 (32,047) | 1,211 | 0.0592 |
| 100 | M-haiku | 45/60 (75%) | 19/27 (70%) | 22.3 / 63.0 | 20.6 / 61.1 | 0.97 | 4,696 (2,257) | 2,457 | 0.0215 |
| 100 | M-opus | 53/60 (88%) | 22/27 (81%) | 6.2 / 11.5 | 4.1 / 7.9 | 1.03 | 5,685 (3,072) | 359 | 0.0239 |
