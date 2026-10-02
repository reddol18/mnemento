# Mnemento

> *Like Leonard's polaroids in **Memento** — a shared, structured record book for LLM agents.*

LLM agents forget between sessions. Mnemento gives them one place to **write facts** and **ask questions in natural language**, answered by exact structured queries — with evidence.

- **Keeper agent** — the only writer; validates, deduplicates, records provenance, asks back when unsure
- **JSON documents + schema registry on SQLite** — flexible storage, but the LLM always knows which fields exist
- **Event log + current state** — every answer can cite the records it counted
- **MCP server** — plug into Claude Code / Claude Desktop

> Status: v0 in progress — storage core and question pipeline + MCP server done; benchmark next.
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

설치(Claude Code): 위 `claude mcp add ...` 한 줄. 개발: `uv sync && uv run pytest`.
현재 상태: 저장소 코어와 질문 파이프라인·MCP 서버 완료, 벤치마크 진행 예정. 기획서는 [docs/PLAN.md](docs/PLAN.md), 설계 결정은 [docs/adr/](docs/adr/).

License: MIT
