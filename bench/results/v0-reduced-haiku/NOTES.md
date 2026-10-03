# v0-reduced-haiku — run notes and limitations

- Frozen hash `4be3dfef6c9b8b3b`, commit `d60878a` (the `+dirty` in report.md comes only from this run's
  untracked output files: results.jsonl, the log and this file — no tracked file was modified).
- Model `haiku` through the Claude Code CLI for every system. Started 2026-10-03 16:00 KST.
- **Reduced measurement: 12/20 questions (D1–D4, U1–U4, H1–H4, first four ids of each set), 3 repetitions,
  Mn at 100 records only.** The remaining 8 questions / repetitions can be added to the same run later.

## What ran

| scale | B0 | B1 | M | Mn |
|---|---|---|---|---|
| 100 | ✅ | ✅ | ✅ | ✅ |
| 1,000 | not measurable (context > 200k) — not run in the reduced plan | ✅ | ✅ | — |
| 10,000 | not measurable | ✅ **1 repetition** (approved separately for cost) | ✅ | — |

The plan was stopped right before the 10,000-record B1 step (held for cost); 10,000-record M was then run
separately into the same run. After user approval, 10,000-record B1 was run **once** (12 calls, 2026-10-03 20:41,
same commit and frozen hash, a $15 cost guard). No failed CLI calls in any step (264 rows, all completed).

## Limitations found during the run (not fixed — the system is frozen during measurement)

1. **D4 harness bias against M.** B0/B1 receive the format hint ("value = share viewed"); M by design receives only
   the question (ADR-0011 §6). Haiku interpreted "먼저 열람되나" as *time to view* (average days) for M, a valid
   reading the grader does not accept. All systems scored low on D4; B0/B1 also miscounted "viewed" as the
   *current* status only.
2. **H2 interpretation error (M).** "열람된 지원" was translated to `status in (viewed, passed)`, which drops
   applications viewed and later rejected; the right filter is `viewed_at exists`. A genuine M error.
3. **H3 / H4 not expressible in QuerySpec v0** (group-size filter; ordering by event time). M fails them by
   construction. In addition, M's answer formatter reports the matched total (10 / 120 / 1,580) as `number` for
   H4 instead of the 3 rows shown — a formatter weakness on top of the capability gap.
4. **U2 at 10,000 records: ambiguous alias in the generated data.** The generator draws English aliases from a
   small pool, so at 10,000 records several companies share the alias "Coresol Data". M correctly refused to guess
   and asked back (`clarify`, graded wrong); the answer key assumes one company. This is a generator flaw that only
   shows at large scale; B1 (1 repetition) also missed U2 there by merging the companies sharing the alias (12 vs 9).
5. H3 has low discriminating power (most companies have ≥2 applications) and the generated data has no duplicate
   company names, so alias grouping is weakly tested (ADR-0011).
6. The dev/unseen split: U1–U4 were written by the implementer; H1–H4 by the directing agent, who knew the
   generator's schema.

## 10,000-record B1 — measured (1 repetition) vs. the estimate

| | estimate (from 100 → 1,000 growth) | measured |
|---|---|---|
| accuracy | — | 5/12 (D2, D3, D4, U3, H2 correct) |
| input tokens per call | ≈ 3.1M | 342k mean (max 1.35M, H3) |
| turns per call | ≈ 35–40 | 20.5 |
| list cost, 12 calls | $4.8–11.5 | **$1.52** |
| time | 25–45 min | 16.6 min (83 s mean) |

B1 did not grow the way the extrapolation assumed: at 10,000 records it explored *less* (fewer turns, fewer tokens
than at 1,000) and answered from partial searches — cheaper, but wrong more often (e.g. D1 22 vs 73, U1 51 vs 123,
H3 17 vs 2,668). U2 at this scale is affected by the alias collision too (B1 merged companies sharing the alias:
12 vs 9). A single repetition: treat its accuracy as indicative.

### Original estimate (kept for the record)

| per call | 100 | 1,000 | growth | 10,000 (extrapolated) |
|---|---|---|---|---|
| input tokens (mean) | 84.5k | 510k (max 5.05M) | ×6.0 | ≈ 3.1M (single calls may exceed 10M) |
| turns (mean) | 11.4 | 21 | ×1.8 | ≈ 35–40 |
| list cost (USD) | 0.063 | 0.159 | ×2.5 | ≈ 0.40 (if cost grows like tokens: ≈ 0.95) |
| time (s) | 55 | 83 | ×1.5 | ≈ 125–210 |

One repetition (12 calls): **≈ 37M input tokens (mostly cache reads), ≈ $4.8–11.5 list price, ≈ 25–45 min.**
Three repetitions: ≈ $15–35, 1.3–2.1 h. Risk: grep output over 13,268 files can push single turns toward the
200k context limit (truncation or failed calls).
