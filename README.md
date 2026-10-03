# Mnemento

> *Like Leonard's polaroids in **Memento** — a shared, structured record book for LLM agents.*

LLM agents forget between sessions. Mnemento gives them one place to **write facts** and **ask questions in natural language**, answered by exact structured queries — with evidence.

- **Keeper agent** — the only writer; validates, deduplicates, records provenance, asks back when unsure
- **JSON documents + schema registry on SQLite** — flexible storage, but the LLM always knows which fields exist
- **Event log + current state** — every answer can cite the records it counted
- **MCP server** — plug into Claude Code / Claude Desktop

> Status: v0 done — storage core, question pipeline, MCP server and a first (reduced) benchmark. v1 (accuracy) next.
> Design: [docs/PLAN.md](docs/PLAN.md) · decisions: [docs/adr/](docs/adr/) · demo log: [docs/demo/0002-demo-log.md](docs/demo/0002-demo-log.md)

## How a question is answered

```
"10/2 사람인 지원 몇 곳?"
  ① interpret   rule-based fast path (no LLM) ─┐   or LLM → QuerySpec (sees only the relevant
                                               │      schema dictionary, never the records)
  ② query       QuerySpec ──code──▶ parameterised SQL on generated, indexed columns
  ③ answer      count / list / groups + evidence ids + warnings (small samples, incomplete
                periods, records too recent to have an outcome, resolved dates and names)
```

- The model never writes SQL and never sees your records; it gets field names, descriptions and allowed values.
- Anything outside the schema dictionary is rejected (and the model is asked once more), so it cannot query fields that do not exist.
- When a question or a write is ambiguous (which company? which record?), the Keeper stores nothing and returns `clarify` with options.
- Each answer carries a trace: per-stage time, LLM calls, input/output tokens and cost.

## Benchmark (v0, measured)

Does a structured record book beat an LLM's own memory? Same fictional job-search history, same questions, same model
(`haiku` via the Claude Code CLI), three ways of remembering:

- **B0 full context** — every memory file in the prompt
- **B1 Claude Code memory** — `MEMORY.md` index (first 200 lines loaded) + one markdown file per record, searched with Grep/Read
- **M Mnemento** — interpret → QuerySpec → SQL → template answer

![Accuracy, time and input tokens per question by number of records](docs/bench/v0-scale-curves.svg)

| records | system | correct (12 questions × 3) | time / question, mean (p95) | input tokens / question | list cost / question |
|---|---|---|---|---|---|
| 100 | B0 full context | 23/36 (64%) | 35.7 s (106 s) | 24.9k | $0.029 |
| 100 | B1 Claude Code memory | **28/36 (78%)** | 55.2 s (151 s) | 84.5k | $0.063 |
| 100 | M Mnemento | 23/36 (64%) | **15.2 s** (42 s) | **3.7k** | **$0.016** |
| 1,000 | B0 | not measurable — prompt exceeds the 200k context | | | |
| 1,000 | B1 Claude Code memory | 18/36 (50%) | 82.8 s (224 s) | 510k | $0.159 |
| 1,000 | M Mnemento | **24/36 (67%)** | **16.3 s** (41 s) | **4.0k** | **$0.017** |
| 10,000 | B1 Claude Code memory ¹ | 5/12 (42%) | 83.0 s (195 s) | 342k | $0.127 |
| 10,000 | M Mnemento | **22/36 (61%)** | **14.9 s** (39 s) | **3.9k** | **$0.016** |

¹ one repetition only (approved separately for cost). Times include ≈2 s of CLI process start per LLM call for every
system; input tokens include the CLI's fixed ≈2.5k (≈6.7k with tools) per call. Values without that overhead, the
dev/unseen split and every raw answer: [report](bench/results/v0-reduced-haiku/report.md) ·
[notes & limitations](bench/results/v0-reduced-haiku/NOTES.md) · method: [ADR-0008](docs/adr/0008-benchmark-methodology.md),
[ADR-0011](docs/adr/0011-benchmark-harness.md).

**Reading it honestly**
- **Small data: Claude Code's own memory was more accurate** (28 vs 23 of 36 at 100 records). Mnemento was 3.6× faster
  and used ~1/23 of the input tokens.
- **As records grow, Mnemento's time and tokens stay flat** (≈15 s, ≈4k tokens at 100, 1,000 and 10,000) while file
  search gets slower, more expensive and less accurate (B1: 78% → 50% → 42%).
- Mnemento's misses are mostly fixable gaps, tracked for v1 ([task 0004](docs/tasks/0004-v1-accuracy.md)): it reads
  "was viewed" as the *current* status (H2), and QuerySpec v0 cannot filter on group size or sort by event time (H3, H4).

**Limitations of this measurement**
- **Reduced:** 12 of 20 questions (first four of each set: dev D1–D4, unseen U1–U4, independently written H1–H4),
  3 repetitions, narration variant only at 100 records; one model (`haiku`).
- **Harness bias on D4:** B0/B1 get an answer-format hint ("value = share viewed"); Mnemento gets only the question and
  read it as *time to view*. All three systems missed D4.
- **Generator flaw at 10,000 records:** English aliases are drawn from a small pool, so several companies share one
  (U2). Mnemento asked back instead of guessing (graded wrong); B1 merged them (also wrong).
- Answer keys come from the generator's ground truth, never from Mnemento; prompts and rules were frozen (hash in the
  report) before measuring and not changed during it.

## Benchmark (v2, measured) — haiku vs opus at 100 records

After v1 (history-aware `reached` filter, `having`, ordering by event time), a fairer harness: the **same answer-format
hint for every system**, a new evaluation set (seed and date different from v0; company aliases unique, ~30% of
records written under the alias), 20 dev questions + **9 unseen questions written by the directing agent**,
3 repetitions, 100 records. Claude Code memory (B1) and Mnemento (M) each on `haiku` (claude-haiku-4-5) and
`opus` (claude-opus-5-5):

| system | dev (20 × 3) | unseen (9 × 3) | total | time / question, mean (p95) | input tokens / question | API-equivalent cost / question |
|---|---|---|---|---|---|---|
| M-haiku | 45/60 | 19/27 | 64/87 (74%) | 22.3 s (63 s) | 4.7k | $0.022 |
| M-opus | 53/60 | 22/27 | 75/87 (86%) | **6.2 s** (11.5 s) | 5.7k | $0.024 |
| B1-haiku | 36/60 | 18/27 | 54/87 (62%) | 44.8 s (124 s) | 59.3k | $0.051 |
| B1-opus | **56/60** | **27/27** | **83/87 (95%)** | 15.9 s (28 s) | 34.6k | $0.059 |

- **With opus, Claude Code's own memory was more accurate at 100 records: B1-opus 83/87 > M-opus 75/87.**
  Mnemento answered 2.6× faster with 1/6 of the input tokens and ~40% of the cost.
- With haiku, Mnemento was ahead (64 vs 54 of 87) at half the cost.
- Mnemento's gap is concentrated in five questions where its interpreter fails the same way on both models
  (event kinds without Korean labels, schema selection missing the referencing record type, ordering by a date field
  instead of event time, "last week of a month", and a formatter bug for event counts) — being fixed in
  [task 0005](docs/tasks/0005-v1.1-fixes.md) and re-measured on a fresh evaluation set (v3) with new unseen questions.

Raw data: [report](bench/results/v2-100-2x2/report.md) · [results](bench/results/v2-100-2x2/results.jsonl).
API-equivalent cost = list price reported by the CLI; the runs used a Claude subscription, not an API key.

## Install as an MCP server (Claude Code)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/<you>/mnemento.git
claude mcp add mnemento -e MNEMENTO_DB="$HOME/.mnemento/mnemento.db" -- uv --directory /path/to/mnemento run mnemento-mcp
```

Tools: `query`, `record`, `get_entity`, `history`, `list_schemas`, `propose_schema`.
Natural-language interpretation uses the Claude Code CLI you are already logged into (`MNEMENTO_LLM=claude-cli`, default model `haiku`; set `MNEMENTO_MODEL` to change). With `MNEMENTO_LLM=none` only the fast path and structured `QuerySpec`s are answered.

For Claude Desktop, add the same command to `claude_desktop_config.json`:

```json
{ "mcpServers": { "mnemento": {
    "command": "uv", "args": ["--directory", "/path/to/mnemento", "run", "mnemento-mcp"],
    "env": { "MNEMENTO_DB": "/path/to/mnemento.db" } } } }
```

## Development

```bash
uv sync
uv run pytest                              # unit tests (no network, LLM is a scripted fake)
uv run python examples/demo_queries.py     # real LLM calls via the claude CLI, writes docs/demo/
```

Layout: `src/mnemento/` — `storage/` (SQLite, WAL, append-only events), `schema/` (dictionary + JSON Schema validation), `ledger.py` (record/replay API), `keeper/` (query pipeline, record tool, identity resolution, schema proposals, LLM adapters), `mcp_server.py`. Domain schemas live in `schemas/`. All example data is fictional.

---

### 한국어

LLM 에이전트는 세션이 끝나면 잊습니다. Mnemento는 여러 에이전트가 **사실을 한 장부에 기록**하고, **자연어로 물으면 정확한 조건 질의로, 근거와 함께** 답하게 해 주는 기록 관리 에이전트입니다.

- **질문 처리**: ① 해석(정형 질문은 LLM 없이, 나머지는 LLM이 스키마 사전만 보고 `QuerySpec` 작성) → ② 질의(코드가 SQL 생성, LLM은 SQL을 쓰지 않음) → ③ 답변(근거 id 목록 + 표본 수·미완료 기간·결과 미확정 경고).
- **기록**: 형식이 맞는 입력은 코드로 바로 검증·저장, 자유 서술은 LLM이 구조화한 뒤 같은 검증을 거칩니다. 대상이 여럿이거나 모르는 회사면 저장하지 않고 되묻습니다.
- **동일 회사 판정**: 사업자번호 → 정규화 이름·별칭. 비슷한 이름은 후보로만 제시하고 자동 병합하지 않습니다.
- **계측**: 질문마다 단계별 시간, LLM 호출 수, 입력·출력 토큰, 비용을 기록합니다.

- **벤치마크(v2, 100건, 같은 형식 힌트, haiku·opus 2×2)**: opus 기준으로는 Claude Code 자체 메모리가 더 정확했다(B1-opus 83/87 > M-opus 75/87). Mnemento는 2.6배 빠르고 입력 토큰은 1/6이었다. haiku 기준으로는 Mnemento가 앞섰다(64 대 54/87). Mnemento의 약점 5가지는 [작업 0005](docs/tasks/0005-v1.1-fixes.md)에서 고친 뒤 새 평가셋(v3)으로 다시 잰다.
- **벤치마크(v0, 축소 측정: 12/20문항·3회 반복·haiku 한 모델)**:
  - 기록 100건에서는 Claude Code 자체 메모리(B1)가 더 정확했다(28 대 23/36). 대신 Mnemento가 3.6배 빠르고 입력 토큰은 약 1/23이었다.
  - 기록이 1,000·10,000건으로 늘어도 Mnemento는 문항당 약 15초·약 4천 토큰으로 일정했다. 반면 파일 검색 방식은 느려지고 비싸지면서 정확도도 떨어졌다(78% → 50% → 42%, 10,000건은 1회 측정).
  - 한계: D4는 형식 힌트가 B0·B1에만 주어진 하네스 편향이 있다. 10,000건에서는 생성기 별칭이 중복된다. 자세한 내용은 위 표와 [결과 노트](bench/results/v0-reduced-haiku/NOTES.md)에 있다.

설치(Claude Code): 위 `claude mcp add ...` 한 줄. 개발: `uv sync && uv run pytest`.
현재 상태: v0 완료(저장소 코어, 질문 파이프라인, MCP 서버, 첫 벤치마크). 다음은 v1 정확도 보강([작업 0004](docs/tasks/0004-v1-accuracy.md)). 기획서는 [docs/PLAN.md](docs/PLAN.md), 설계 결정은 [docs/adr/](docs/adr/).

License: MIT
