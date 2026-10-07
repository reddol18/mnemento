# Mnemento

[![CI](https://github.com/reddol18/mnemento/actions/workflows/ci.yml/badge.svg)](https://github.com/reddol18/mnemento/actions/workflows/ci.yml)

> *Like Leonard's polaroids in **Memento** — a shared, structured record book for LLM agents.*

LLM agents forget between sessions. Mnemento gives them one place to **write facts** and **ask questions in natural language**, answered by exact structured queries — with evidence.

- **Keeper agent** — the only writer; validates, deduplicates, records provenance, asks back when unsure
- **JSON documents + schema registry on SQLite** — flexible storage, but the LLM always knows which fields exist
- **Event log + current state** — every answer can cite the records it counted
- **MCP server** — plug into Claude Code / Claude Desktop

> Status: v1.3 — storage core, question pipeline, MCP server, measured three times against Claude Code's own memory
> (benchmarks on v1.1).
>
> **Latest result (v3, opus, questions the system was not developed on):** the same accuracy as Claude Code's own
> memory — 27/27 vs 27/27 at 100 records, 24/27 vs 24/27 at 1,000 — and at 1,000 records **6× faster at 1/8 of the
> cost**. Earlier rounds lost on accuracy (v0, v2); they are kept below as history.
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
- Every question is kept in a local query log (question, interpretation, QuerySpec, SQL and parameters, result count, evidence ids, warnings, timings, cost). The `query_log` tool shows recent questions, those with warnings or errors, and questions whose SQL changed between askings (`diverging`). Bounded to 10,000 rows / 50 MB; `MNEMENTO_QUERY_LOG=off` turns it off ([ADR-0015](docs/adr/0015-query-log.md)).

## Beyond job search: investment records (fictional example)

The same record book holds a second domain without new query code — only new schemas
([examples/investment](examples/investment): `security`, `trade`, `decision`; task 0008 step 3).

```
"가상바이오(900001) 몇 번 샀어?"           -> trade, count, security resolved by its code (identifier)  -> 2
"올해 손절한 종목은?"                       -> trade, sell, pnl < 0, this year                            -> list
"왜 그 종목은 안 사기로 했지?"              -> the reason where it was written, quoted as recorded        -> text + evidence
```

- **Identifiers first**: a field marked `identifier` (a stock code, a business registration number) is matched
  wherever it appears in the question — "nickname(900001)" finds the security even if the nickname is unknown
  ([ADR-0019](docs/adr/0019-identifier-fields.md)). Names alone that only look similar are offered as candidates, never
  linked.
- **Relation notes**: a schema can say how it relates to another ("a trade and the decision behind it may be the same
  event — count buys from trade"); the interpreter is shown related types and counts each event from one type.
- **Copies of files you keep elsewhere**: `mnemento.importer` makes repeated imports safe — the same source key is the
  same record, a changed fact becomes a `corrected` event, a changed state (e.g. buy criteria) an `updated` event at
  the source's own date, and records that disappeared from the source are reported, never deleted.
- Used locally on the author's own investment records (trades, decisions, buy criteria, holdings); none of that data is
  in this repository. Daily valuations are left for price series (next step, [ADR-0016](docs/adr/0016-schema-kind-entity-series.md)).

## Measurements over time (series, fictional example)

Some records are not "things that change state" but values measured again and again — body weight, daily spending,
prices. A schema can be `kind: "series"` (a key such as `person`, a time field, numeric measures); points are stored
one row per key and time, in batches that can be reverted as a whole ([ADR-0016](docs/adr/0016-schema-kind-entity-series.md)).

```
"가상인 3월 평균 몸무게 얼마였어?"                    -> weight, avg kg, March                       -> 68.7
"샘플인은 6월부터 8월 말까지 몸무게가 얼마나 변했어?"  -> change = last - first in the period        -> +0.9
"8월에 가상인 몸무게가 최근 4주 평균보다 낮았던 날은?" -> 28-day window + compare with the window     -> 20 days
```

- Period, `group_by` key or time bucket (day/week/month/year), `avg`/`min`/`max`/`sum`, `first`/`last`/`change`/
  `change_pct`, moving windows (`unit: points|days`, computed over the history before the asked period) and
  comparisons with a window or a value. SQL is generated by code, as for every other question.
- Warnings: days without a point in the asked period, small samples, a period still in progress.
- MCP: `record_series` (one batch, all or nothing; a point already stored is updated and the batch keeps the old
  value), `revert_series_batch`.
- Evaluation: [bench/series_eval.py](bench/series_eval.py) — fictional questions with answer keys computed from the generator. Dev (10, written with the code): M-haiku 9/10,
  M-opus 9/10. **Unseen** (7, written by someone who did not see the code, run once): **M-haiku 4/7, M-opus 4/7**
  (5/7 each when the right value is anywhere in the answer). The misses are honest gaps: a difference of two periods
  in one number, and counting days *without* a point, are not expressible yet; a right change listed after first/last
  is graded on the first measure ([dev](bench/results/series-dev-2/report.md), [unseen](bench/results/series-unseen-1/report.md)).

## Benchmark (v3, latest)

Measured on the frozen version `2c56c658bc5f10f9` (v1.1). v1.2 (names of referenced records in answers, unknown
dates and event-time precision — [ADR-0013](docs/adr/0013-date-precision.md), a benchmark formatter fix) came from
using Mnemento on real records afterwards and is **not re-measured**; v1.3 (store first, organize later —
[ADR-0014](docs/adr/0014-store-first-organize-later.md)) was only checked with a regression run on dev questions.
Later changes (task 0008) are checked the same way. In its step 2 the benchmark converter for Mnemento's answers was
fixed — an average question answered with extra count measures was recorded as the record count (it cost M-haiku two
of the D5 misses at 1,000 records below). The fix changes the frozen hash; the scores below stay as measured.
Same fictional job-search history rendered three ways (ADR-0011), the **same answer-format hint for every system**,
evaluation set v3 (own seed; reference date Monday 2027-02-01 so weeks and month ends matter), **28 dev questions +
9 unseen questions written by the directing agent before measuring**, 3 repetitions, prompts and rules frozen
(hash in the report). Claude Code memory = `MEMORY.md` index + one markdown file per record, searched with Grep/Read.

| records | system | dev ¹ | **unseen** | time / question, mean (p95) | input tokens / question | API-equivalent cost / question |
|---|---|---|---|---|---|---|
| 100 | Mnemento · opus | 84/84 | **27/27** | **5.9 s** (9 s) | 6.1k | $0.025 |
| 100 | Claude Code memory · opus | 77/84 | **27/27** | 14.8 s (24 s) | 36.9k | $0.058 |
| 100 | Mnemento · haiku | 76/84 | 24/27 | 26.2 s (75 s) | 5.1k | $0.025 |
| 100 | Claude Code memory · haiku | 63/84 | 19/27 | 42.4 s (97 s) | 61.0k | $0.050 |
| 1,000 | Mnemento · opus | 79/84 | **24/27** | **5.8 s** (10 s) | 6.2k | **$0.025** |
| 1,000 | Claude Code memory · opus | 74/84 | **24/27** | 35.6 s (127 s) | 123.3k | $0.205 |
| 1,000 | Mnemento · haiku | 72/84 | 24/27 | 24.8 s (67 s) | 5.2k | $0.024 |
| 1,000 | Claude Code memory · haiku | 39/84 | 10/27 | 77.0 s (224 s) | 454.0k | $0.149 |

¹ The dev questions were used while fixing Mnemento (v1, v1.1); an advantage on them may be overfitting. Compare on
the unseen column.

- **Accuracy on unseen questions: on par with Claude Code's own memory on opus** (100 records 27/27 = 27/27;
  1,000 records 24/27 = 24/27). **On haiku, Mnemento is clearly ahead** (24 vs 19 of 27 at 100 records, 24 vs 10 at
  1,000), and Claude Code memory on haiku falls apart as records grow (unseen 70% → 37%).
- **Cost and speed at 1,000 records:** 6× faster, 1/20 of the input tokens, 1/8 of the cost. Claude Code memory got
  slower and more expensive as records grew (2.4× the time, 3.6× the cost from 100 to 1,000); Mnemento stayed flat.
- Every question, every system, with a one-line reason for each miss: **[results by question](docs/bench/results-by-question.md)** ·
  raw data: [report](bench/results/v3-haiku-opus/report.md) · [results](bench/results/v3-haiku-opus/results.jsonl).

**Limitations (current)**
- **Elapsed-time filters are not expressible** ("viewed more than three days after applying", W5): QuerySpec v1.1 can
  average the time between two events but cannot filter on it. Haiku said so (clarify); opus found a workaround that
  happened to hit an accepted reading at 100 records and failed at 1,000.
- **Vague criteria** ("quickly viewed", D5) make the haiku interpreter ask back instead of choosing a default.
- Both are addressed after this measurement ([ADR-0018](docs/adr/0018-elapsed-filter-and-vague-defaults.md): an
  `elapsed` condition, and default readings for vague words shown as warnings). W5 prompted the change, so its
  score here cannot show the gain; that needs new unseen questions, which have not been measured yet.
- **A harness formatter bug** cost Mnemento D3 at 1,000 records: it answered "187 records, showing 50", the benchmark
  recorded 50 because the plan carried `limit: 50` from the format hint. Scored as measured.
- One fictional domain and generator, 37 questions, 3 repetitions, a format hint that tells every system the expected
  answer shape; the format hint is not how people ask. API-equivalent cost = list price reported by the CLI (the runs
  used a Claude subscription, not an API key).

## Benchmark history — v2 (haiku vs opus at 100 records)

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

## Benchmark history — v0 (first, reduced, haiku only)

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

## Install as an MCP server (Claude Code)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/reddol18/mnemento.git
claude mcp add mnemento -e MNEMENTO_DB="$HOME/.mnemento/mnemento.db" -- uv --directory /path/to/mnemento run mnemento-mcp
```

Tools: `query`, `record`, `get_entity`, `history`, `list_schemas`, `propose_schema`, `apply_schema_proposal`, `query_log`, `purge_query_log`.
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
- **조회 로그**: 모든 질문을 로컬 DB에 남깁니다(질문, 해석, QuerySpec, SQL과 파라미터, 결과 수, 근거 id, 경고, 시간, 비용). `query_log` 도구로 최근 질문, 경고·오류가 붙은 질문, 같은 질문인데 SQL이 달라진 것(`diverging`)을 볼 수 있습니다. 최대 10,000행 또는 50MB, `MNEMENTO_QUERY_LOG=off`로 끕니다([ADR-0015](docs/adr/0015-query-log.md)).
- **두 번째 도메인(투자 기록, 가상 예시)**: 질의 코드는 그대로 두고 스키마만 더해 투자 기록(종목·체결·판단)을 담습니다([examples/investment](examples/investment)). 질문에 종목 코드·사업자번호 같은 식별자가 있으면 그것으로 먼저 찾습니다([ADR-0019](docs/adr/0019-identifier-fields.md)). 스키마 간 관계 설명으로 같은 사건을 두 번 세지 않습니다. `mnemento.importer`로 다른 곳에 두는 파일을 여러 번 가져와도 안전합니다(바뀐 사실은 정정, 바뀐 상태는 원본 날짜의 갱신, 사라진 기록은 보고만). 작성자의 실제 투자 기록으로 로컬에서 쓰고 있으며, 그 데이터는 저장소에 없습니다.

- **벤치마크(v3, 최신)**: opus 기준으로 **개발에 쓰지 않은 문항의 정확도는 Claude Code 자체 메모리와 같다**(100건 27/27 동률, 1,000건 24/27 동률). 1,000건에서는 **6배 빠르고 비용은 1/8**이다. 개발 문항에서 앞선 부분은 그 문항으로 고쳤기 때문에 과적합일 수 있다. 남은 약점은 경과 시간 조건 필터를 표현할 수 없다는 점과 모호한 기준이다(측정 이후 [ADR-0018](docs/adr/0018-elapsed-filter-and-vague-defaults.md)로 보완. W5가 계기였으므로 개선 효과는 새 미노출 문항으로 측정해야 하며, 아직 측정하지 않았다). 문항별 결과는 [여기](docs/bench/results-by-question.md).
- **이력 — 벤치마크(v2, 100건, 같은 형식 힌트, haiku·opus 2×2)**: opus 기준으로는 Claude Code 자체 메모리가 더 정확했다(B1-opus 83/87 > M-opus 75/87). Mnemento는 2.6배 빠르고 입력 토큰은 1/6이었다. haiku 기준으로는 Mnemento가 앞섰다(64 대 54/87). Mnemento의 약점 5가지는 [작업 0005](docs/tasks/0005-v1.1-fixes.md)에서 고친 뒤 새 평가셋(v3)으로 다시 잰다.
- **이력 — 벤치마크(v0, 축소 측정: 12/20문항·3회 반복·haiku 한 모델)**:
  - 기록 100건에서는 Claude Code 자체 메모리(B1)가 더 정확했다(28 대 23/36). 대신 Mnemento가 3.6배 빠르고 입력 토큰은 약 1/23이었다.
  - 기록이 1,000·10,000건으로 늘어도 Mnemento는 문항당 약 15초·약 4천 토큰으로 일정했다. 반면 파일 검색 방식은 느려지고 비싸지면서 정확도도 떨어졌다(78% → 50% → 42%, 10,000건은 1회 측정).
  - 한계: D4는 형식 힌트가 B0·B1에만 주어진 하네스 편향이 있다. 10,000건에서는 생성기 별칭이 중복된다. 자세한 내용은 위 표와 [결과 노트](bench/results/v0-reduced-haiku/NOTES.md)에 있다.

설치(Claude Code): 위 `claude mcp add ...` 한 줄. 개발: `uv sync && uv run pytest`.
현재 상태: v1.3(벤치마크 3회는 v1.1 기준). v1.2는 실제 기록으로 써 보며 나온 개선(답에 회사명 표시, 날짜·시각 정밀도)이고, v1.3은 "먼저 저장하고 나중에 정리"(스키마에 없는 필드·값도 저장하고 바로 조회, 정리는 사용자 승인)이다. v1.3은 회귀 측정만 했다. 이후 변경(작업 0008)도 회귀로만 확인하며, 그 ② 단계에서 벤치 답 변환기의 결함(평균을 묻는 질문에 개수 측정값이 함께 있으면 레코드 수를 기록)을 고쳤다. 이 결함 때문에 위 v3 표의 1,000건 M-haiku D5 오답 중 2건이 생겼다. frozen hash는 바뀌었고, 표의 점수는 측정한 그대로 둔다. 기획서는 [docs/PLAN.md](docs/PLAN.md), 설계 결정은 [docs/adr/](docs/adr/).

License: MIT
