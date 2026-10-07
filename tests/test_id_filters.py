"""Issue #5: an id filter that names no record (an importer's source key, or a name where the id belongs) is mapped to
the record it means or asked about, never answered with a silent 0 — and plans that found nothing are not cached.
Fictional investment records from test_investment; all LLM calls go to ScriptedLLM."""

from mnemento.importer import stable_id
from mnemento.keeper import Keeper, ScriptedLLM

from tests.test_investment import NOW, SOURCE, led, run_import  # noqa: F401 (led is a fixture)


def _events_spec(value):
    return {"kind": "query", "spec": {"source": "events", "entity_type": "decision", "mode": "list",
                                      "filters": [{"field": "entity_id", "op": "eq", "value": value}],
                                      "interpretation": "그 판단 기록의 변경 이력"}}


def test_source_key_in_an_id_filter_is_mapped_to_the_record(led):
    """The interpreter wrote the importer's source key where the entity id belongs (it looks like an id to it)."""
    run_import(led, SOURCE)
    k = Keeper(led, ScriptedLLM([_events_spec("real:900002:exit_decision_20260901")]))
    ans = k.ask("샘플전자 정리 판단은 언제 바뀌었어?", now=NOW)
    assert ans.status == "answered" and ans.result["total"] == 1
    target = stable_id("decision", "real:900002:exit_decision_20260901")
    assert ans.evidence and ans.result["rows"][0]["entity_id"] == target
    assert any("source key" in w and target in w for w in ans.warnings)


def test_unknown_id_in_a_filter_asks_and_is_not_cached(led):
    run_import(led, SOURCE)
    llm = ScriptedLLM([_events_spec("criteria:900009"), _events_spec("criteria:900009")])
    k = Keeper(led, llm)
    q = "가상바이오 매수 기준 마지막으로 바꾼 날?"
    first = k.ask(q, now=NOW)
    assert first.status == "clarify" and "criteria:900009" in first.text
    second = k.ask(q, now=NOW)  # interpreted again: a plan that named no record was not kept
    assert second.status == "clarify" and second.trace["path"] == "llm" and len(llm.calls) == 2


def test_a_name_in_an_id_filter_of_a_reference_field_is_resolved(led):
    run_import(led, SOURCE)
    spec = {"entity_type": "trade", "mode": "count",
            "filters": [{"field": "security_id", "op": "eq", "value": "가상바이오"}], "interpretation": "가상바이오 체결 수"}
    k = Keeper(led, ScriptedLLM([{"kind": "query", "spec": spec}]))
    ans = k.ask("가상바이오 체결 몇 건이야", now=NOW)
    assert ans.status == "answered" and ans.result["total"] == 2
    assert any("가상바이오" in w and "sec_900001" in w for w in ans.warnings)


def test_zero_row_plans_are_not_cached(led):
    run_import(led, SOURCE)
    spec = {"entity_type": "trade", "mode": "count", "interpretation": "모의 계좌 매도 수",
            "filters": [{"field": "account", "op": "eq", "value": "paper"}, {"field": "side", "op": "eq", "value": "sell"}]}
    llm = ScriptedLLM([{"kind": "query", "spec": spec}, {"kind": "query", "spec": spec}])
    k = Keeper(led, llm)
    q = "모의 계좌에서 판 거 몇 번이야"
    assert k.ask(q, now=NOW).result["total"] == 0
    again = k.ask(q, now=NOW)
    assert again.result["total"] == 0 and again.trace["path"] == "llm" and len(llm.calls) == 2


def test_query_log_empty_view_shows_zero_row_answers_by_path(led):
    from mnemento.keeper.query.log import QueryLog

    run_import(led, SOURCE)
    k = Keeper(led, ScriptedLLM())
    k.pipeline.query_log = QueryLog(led.storage)
    k.ask("a", spec={"entity_type": "trade", "mode": "count",
                     "filters": [{"field": "account", "op": "eq", "value": "paper"},
                                 {"field": "side", "op": "eq", "value": "sell"}]}, now=NOW)
    k.ask("b", spec={"entity_type": "trade", "mode": "count"}, now=NOW)
    empty = k.query_log("empty", n=10)
    assert [e["question"] for e in empty["entries"]] == ["a"]
    assert k.query_log("empty", n=10, path="cache")["entries"] == []
