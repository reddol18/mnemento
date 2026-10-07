Model `haiku` via Claude Code CLI · run `step4b-regression` · frozen `48ab164b72de8dee` · git `776dc0349fabcf70ff997172d1d3d8e75036b06b+dirty`

| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) | no answer / clarify |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | M-haiku | 26/28 (93%) | — | 24.4 / 53.0 | 22.8 / 51.5 | 0.96 | 7,089 (4,653) | 2,520 | 0.0179 | 0 |
| 100 | M-opus | 28/28 (100%) | — | 6.0 / 13.7 | 4.2 / 12.0 | 0.93 | 8,126 (5,780) | 334 | 0.0267 | 0 |
