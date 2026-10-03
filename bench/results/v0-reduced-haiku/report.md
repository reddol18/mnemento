Model `haiku` via Claude Code CLI · run `v0-reduced-haiku` · frozen `4be3dfef6c9b8b3b` · git `d60878a06f48d16537e13679fa9f57cc2266506a+dirty`

**Reduced measurement: 12/20 questions (D1-D4, U1-U4, H1-H4), 3 repetitions, Mn at 100 only.**

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 100 | B0 full context | 9/12 (75%) | 14/24 (58%) | 35.7 / 106.1 | 33.9 / 104.1 | 1.00 | 24,926 (22,400) | 4,923 | 0.0288 |
| 100 | B1 Claude Code memory (MEMORY.md + Grep/Read) | 9/12 (75%) | 19/24 (79%) | 55.2 / 151.0 | 53.2 / 148.7 | 1.00 | 84,498 (77,800) | 6,776 | 0.0632 |
| 100 | M Mnemento (template answer) | 9/12 (75%) | 14/24 (58%) | 15.2 / 41.9 | 13.7 / 38.5 | 0.86 | 3,686 (1,510) | 1,652 | 0.0156 |
| 100 | M+ Mnemento + LLM narration | 9/12 (75%) | 14/24 (58%) | 27.0 / 59.9 | 23.7 / 56.5 | 1.86 | 5,578 (877) | 2,896 | 0.0240 |
| 1,000 | B1 Claude Code memory (MEMORY.md + Grep/Read) | 6/12 (50%) | 12/24 (50%) | 82.8 / 224.3 | 79.2 / 214.2 | 1.00 | 509,712 (503,014) | 9,118 | 0.1594 |
| 1,000 | M Mnemento (template answer) | 10/12 (83%) | 14/24 (58%) | 16.3 / 41.0 | 14.7 / 39.4 | 0.89 | 4,029 (1,784) | 1,831 | 0.0170 |
| 10,000 | B1 Claude Code memory (MEMORY.md + Grep/Read) | 3/4 (75%) | 2/8 (25%) | 83.0 / 194.8 | 66.1 / 169.8 | 1.00 | 342,196 (335,498) | 7,931 | 0.1267 |
| 10,000 | M Mnemento (template answer) | 9/12 (75%) | 13/24 (54%) | 14.9 / 39.4 | 13.3 / 37.8 | 0.92 | 3,926 (1,610) | 1,577 | 0.0157 |
