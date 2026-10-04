"""Query log (ADR-0015): one row per question on every path, never blocking the answer, diverging
interpretations found, retention limits kept. All LLM calls go to ScriptedLLM (no network)."""

from datetime import datetime

import anyio
import pytest
from mcp import Client

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.log import QueryLog
from mnemento.mcp_server import create_server

NOW = datetime.fromisoformat(DEMO_NOW)

VIEWED_SPEC = {"entity_type": "application", "mode": "count",
               "filters": [{"field": "status", "op": "eq", "value": "viewed"}],
               "interpretation": "applications that were viewed"}


@pytest.fixture
def led():
    ledger = open_demo()
    yield ledger
    ledger.close()


def rows(led):
    return led.storage.fetch_all("SELECT * FROM query_log ORDER BY id")


def test_every_path_leaves_one_row(led):
    k = Keeper(led, ScriptedLLM([{"kind": "query", "spec": VIEWED_SPEC},
                                 {"kind": "clarify", "clarify_question": "Which period?", "options": ["a", "b"]}]))
    k.ask("10/2 사람인 지원 몇 곳?", now=NOW, caller="agent_a")            # fast
    k.ask("회사가 열어본 지원 몇 개?", now=NOW)                               # llm
    k.ask("회사가 열어본 지원 몇 개?", now=NOW)                               # cache
    k.ask("열람 수", spec=VIEWED_SPEC, now=NOW)                               # structured
    k.ask("app_o02", now=NOW)                                                 # entity
    k.ask("요즘 반응 좋은 곳?", now=NOW)                                      # clarify (llm)
    Keeper(led, None).ask("요즘 반응 좋은 곳?", now=NOW)                       # error: no LLM
    got = [(r["path"], r["status"]) for r in rows(led)]
    assert got == [("fast", "answered"), ("llm", "answered"), ("cache", "answered"),
                   ("structured", "answered"), ("entity", "answered"), ("llm", "clarify"), ("none", "error")]
    first = rows(led)[0]
    assert first["caller"] == "agent_a" and rows(led)[1]["caller"] == "unknown"
    assert first["question_norm"] == "<DATE> 사람인 지원 몇 곳"
    assert first["result_total"] == 13 and "SELECT" in first["sql"]
    assert '"interpret"' in first["stages_ms"] and '"application"' in first["schema_versions"]
    llm_row = rows(led)[1]
    assert llm_row["interpretation"] == "applications that were viewed" and '"calls":[{' in llm_row["llm"]
    assert rows(led)[6]["error"].startswith("This question needs the LLM")


def test_evidence_is_capped_but_total_kept(led):
    from mnemento.keeper.query import log as qlog

    k = Keeper(led, ScriptedLLM())
    old = qlog.MAX_EVIDENCE
    qlog.MAX_EVIDENCE = 3
    try:
        ans = k.ask("지원 목록", spec={"entity_type": "application", "mode": "list"}, now=NOW)
    finally:
        qlog.MAX_EVIDENCE = old
    entry = k.query_log("recent", n=1)["entries"][0]
    assert len(entry["evidence"]) == 3 and entry["result_total"] == ans.result["total"] > 3


def test_log_failure_never_blocks_the_answer(led, monkeypatch):
    k = Keeper(led, ScriptedLLM())

    def boom(row):
        raise RuntimeError("disk full")

    monkeypatch.setattr(led.storage, "insert_query_log", boom)
    ans = k.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    assert ans.status == "answered" and ans.result["total"] == 13
    assert k.pipeline.query_log.write_errors == 1


def test_unexpected_exception_is_logged_then_raised(led, monkeypatch):
    from mnemento.keeper.query import pipeline

    def broken(*a, **kw):
        raise RuntimeError("compiler bug")

    monkeypatch.setattr(pipeline, "compile_spec", broken)
    k = Keeper(led, ScriptedLLM())
    with pytest.raises(RuntimeError):
        k.ask("열람 수", spec=VIEWED_SPEC, now=NOW)
    r = rows(led)[-1]
    assert r["status"] == "error" and "compiler bug" in r["error"]


def test_same_question_with_different_sql_is_diverging(led):
    k = Keeper(led, ScriptedLLM())
    k.ask("열람된 거 몇 개?", spec=VIEWED_SPEC, now=NOW)
    k.ask("열람된 거 몇 개?", spec=VIEWED_SPEC, now=NOW)
    assert k.query_log("diverging")["groups"] == []
    other = {**VIEWED_SPEC, "filters": [{"field": "viewed_at", "op": "exists"}]}
    k.ask("열람된 거 몇 개?", spec=other, now=NOW)
    k.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    k.ask("9/30 사람인 지원 몇 곳?", now=NOW)  # same pattern, other date -> same SQL, other params: fine
    groups = k.query_log("diverging")["groups"]
    assert [g["question_norm"] for g in groups] == ["열람된 거 몇 개"]
    variants = groups[0]["variants"]
    assert len(variants) == 2 and sorted(v["times"] for v in variants) == [1, 2]
    assert all(r["diverging"] == (r["question_norm"] == "열람된 거 몇 개") for r in rows(led))


def test_views_filter_warnings_errors_and_path(led):
    k = Keeper(led, ScriptedLLM())
    k.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    k.ask("9/1 사람인 지원 몇 곳?", now=NOW)  # nothing that day -> "no result" warning
    Keeper(led, None).ask("요즘 반응 좋은 곳?", now=NOW)
    assert all(e["warnings"] for e in k.query_log("warnings")["entries"])
    assert [e["question"] for e in k.query_log("errors")["entries"]] == ["요즘 반응 좋은 곳?"]
    fast = k.query_log("path", path="fast")
    assert {e["path"] for e in fast["entries"]} == {"fast"} and fast["by_path"]["fast"] == 2
    assert k.query_log("recent", since="2999-01-01T00:00:00+09:00")["entries"] == []
    with pytest.raises(ValueError):
        k.query_log("nope")
    with pytest.raises(ValueError):
        k.query_log("path")


def test_retention_keeps_errors_and_diverging_first(led):
    log = QueryLog(led.storage, max_rows=20, check_every=1)
    k = Keeper(led, ScriptedLLM())
    k.pipeline.query_log = log
    k.ask("열람된 거 몇 개?", spec=VIEWED_SPEC, now=NOW)
    k.ask("열람된 거 몇 개?", spec={**VIEWED_SPEC, "filters": [{"field": "viewed_at", "op": "exists"}]}, now=NOW)
    for i in range(1, 29):
        k.ask(f"9/{i} 사람인 지원 몇 곳?", now=NOW)
    left = rows(led)
    assert len(left) <= 20
    assert sum(r["diverging"] for r in left) == 2  # the two oldest rows survived
    assert left[-1]["question"] == "9/28 사람인 지원 몇 곳?"


def test_retention_by_size(led):
    log = QueryLog(led.storage, max_rows=10_000, max_bytes=20_000, check_every=1)
    k = Keeper(led, ScriptedLLM())
    k.pipeline.query_log = log
    for _ in range(40):
        k.ask("지원 목록", spec={"entity_type": "application", "mode": "list"}, now=NOW)
    total = led.storage.fetch_all("SELECT SUM(size) AS b FROM query_log")[0]["b"]
    assert total <= 20_000


def test_purge_and_switch_off(led, monkeypatch):
    k = Keeper(led, ScriptedLLM())
    k.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    assert k.purge_query_log("2000-01-01T00:00:00+09:00") == 0
    assert k.purge_query_log() == 1 and rows(led) == []
    monkeypatch.setenv("MNEMENTO_QUERY_LOG", "off")
    off = Keeper(led, ScriptedLLM())
    off.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    assert rows(led) == [] and off.query_log()["disabled"]
    monkeypatch.setenv("MNEMENTO_QUERY_LOG", "500")
    assert Keeper(led, ScriptedLLM()).pipeline.query_log.max_rows == 500


def test_query_log_over_mcp(led):
    server = create_server(Keeper(led, ScriptedLLM()))

    async def go():
        async with Client(server) as c:
            await c.call_tool("query", {"question": "app_o02", "by": "agent_b"})
            await c.call_tool("query", {"question": "app_o03"})
            recent = await c.call_tool("query_log", {"view": "recent", "n": 5})
            bad = await c.call_tool("query_log", {"view": "nope"})
            purge = await c.call_tool("purge_query_log", {})
            return recent, bad, purge

    recent, bad, purge = anyio.run(go)
    entries = recent.structured_content["entries"]
    assert [e["question"] for e in entries] == ["app_o03", "app_o02"]
    assert entries[1]["caller"] == "agent_b"
    assert entries[0]["caller"] != "unknown"  # the MCP client's own name
    assert bad.is_error and purge.structured_content["deleted"] == 2
