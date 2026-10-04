"""Question pipeline: interpretation guard, clarification, QuerySpec -> SQL, warnings, fast path.
All LLM calls go to ScriptedLLM (no network)."""

from datetime import datetime

import pytest

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.compile import compile_spec
from mnemento.keeper.query.reltime import resolve_relative
from mnemento.keeper.query.rules import parse_simple
from mnemento.keeper.query.spec import QuerySpec, validate_spec

NOW = datetime.fromisoformat(DEMO_NOW)

Q4_SPEC = {
    "entity_type": "application", "mode": "aggregate",
    "filters": [{"field": "expected_rate", "op": "in", "value": ["top10", "top30"]}],
    "group_by": [{"field": "applied_at", "bucket": "month"}, {"field": "expected_rate"}],
    "measures": [{"name": "viewed", "agg": "count_if", "where": [{"field": "viewed_at", "op": "exists"}]}],
    "interpretation": "view rate by month and expected rate",
}


@pytest.fixture(scope="module")
def demo():
    led = open_demo()
    yield led
    led.close()


def schemas_of(led):
    return {n: led.schemas.get(n) for n in led.schemas.names()}


def keeper(led, *responses):
    llm = ScriptedLLM(list(responses))
    return Keeper(led, llm), llm


def query_out(spec):
    return {"kind": "query", "spec": spec}


# ---- ① interpretation guard ---------------------------------------------------------------

def test_spec_with_field_outside_schema_is_rejected_then_retried(demo):
    bad = {"entity_type": "application", "mode": "count",
           "filters": [{"field": "salary", "op": "gte", "value": 5000}]}
    good = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "status", "op": "eq", "value": "viewed"}]}
    k, llm = keeper(demo, query_out(bad), query_out(good))
    ans = k.ask("연봉 높은데 열람된 곳 몇 개?", now=NOW)
    assert ans.status == "answered"
    assert len(llm.calls) == 2
    assert "salary" in llm.calls[1]["prompt"] and "rejected" in llm.calls[1]["prompt"]
    assert ans.trace["totals"]["llm_calls"] == 2


def test_spec_outside_schema_twice_becomes_clarification(demo):
    bad = {"entity_type": "application", "mode": "count",
           "filters": [{"field": "salary", "op": "gte", "value": 5000}]}
    k, _ = keeper(demo, query_out(bad), query_out(bad))
    ans = k.ask("연봉 5천 이상 지원 몇 곳?", now=NOW)
    assert ans.status == "clarify"
    assert "salary" in ans.text
    assert ans.sql is None


@pytest.mark.parametrize(
    "spec,needle",
    [
        ({"entity_type": "application", "mode": "count",
          "filters": [{"field": "status", "op": "eq", "value": "ghosted"}]}, "not allowed"),
        ({"entity_type": "applications", "mode": "count"}, "unknown entity_type"),
        ({"entity_type": "application", "mode": "count",
          "filters": [{"field": "applied_at", "op": "eq", "value": "yesterday"}]}, "YYYY-MM-DD"),
        ({"entity_type": "application", "mode": "aggregate",
          "group_by": [{"field": "status", "bucket": "month"}]}, "needs a date field"),
        ({"entity_type": "application", "mode": "count",
          "filters": [{"field": "platform", "op": "name_is", "value": "x"}]}, "reference fields"),
        ({"entity_type": "application", "mode": "aggregate",
          "measures": [{"name": "x", "agg": "count_if"}]}, "needs `where`"),
    ],
)
def test_validate_spec_errors(demo, spec, needle):
    errs = validate_spec(QuerySpec.model_validate(spec), schemas_of(demo))
    assert any(needle in e for e in errs), errs


def test_ambiguous_question_returns_clarification_without_query(demo):
    clar = {"kind": "clarify", "clarify_question": "Which period do you mean?",
            "options": ["this month", "last 30 days", "all time"]}
    k, _ = keeper(demo, clar)
    ans = k.ask("요즘 반응 좋은 곳?", now=NOW)
    assert ans.status == "clarify"
    assert ans.options == ["this month", "last 30 days", "all time"]
    assert ans.sql is None and ans.evidence == []


def test_interpreter_sees_dictionary_not_records(demo):
    good = {"entity_type": "application", "mode": "list",
            "filters": [{"field": "status", "op": "eq", "value": "viewed"}]}
    k, llm = keeper(demo, query_out(good))
    k.ask("열람됐는데 결과 없는 곳은?", now=NOW)
    prompt = llm.calls[0]["prompt"]
    assert "status" in prompt and "viewed [열람/열람됨]" in prompt  # allowed values + labels
    assert "observed values: top30, top10" in prompt  # by frequency  # vocabulary of a free-text field
    assert "app_o0" not in prompt and "가상기업" not in prompt  # no records, no company names
    assert "* posting" in prompt  # referenced by application.posting_id -> included


def test_schema_selection_keeps_prompt_small(demo):
    from mnemento.keeper.query.interpret import select_schemas

    schemas = schemas_of(demo)
    # a company question also needs the records that point at companies (applications, postings)
    assert {s.name for s in select_schemas("회사 목록", schemas)} == {"company", "application", "posting"}
    assert {s.name for s in select_schemas("사람인 지원 몇 곳", schemas)} == {"application", "company", "posting"}
    assert len(select_schemas("무엇이든", schemas)) == 3  # nothing matched -> fall back to all


# ---- ② QuerySpec -> SQL --------------------------------------------------------------------

def test_compile_count_uses_generated_columns_and_bound_params(demo):
    spec = QuerySpec.model_validate({
        "entity_type": "application", "mode": "count",
        "filters": [{"field": "platform", "op": "eq", "value": "saramin"},
                    {"field": "applied_at", "op": "eq", "value": "@today-1d"},
                    {"field": "reason", "op": "contains", "value": "50%_off"}]})
    c = compile_spec(spec, demo.schemas.get("application"), NOW)
    assert "f_platform = ?" in c.sql and "f_applied_at = ?" in c.sql
    assert "json_extract(doc, '$.reason') LIKE ? ESCAPE" in c.sql
    assert c.params == ["application", "saramin", "2026-10-02", "%50\\%\\_off%"]
    assert c.resolved_dates == {"@today-1d": "2026-10-02"}
    assert "saramin" not in c.sql  # values are never inlined


def test_compile_aggregate_with_buckets_and_count_if(demo):
    c = compile_spec(QuerySpec.model_validate(Q4_SPEC), demo.schemas.get("application"), NOW)
    assert "substr(f_applied_at, 1, 7) AS g0" in c.sql
    assert "SUM(CASE WHEN f_viewed_at IS NOT NULL THEN 1 ELSE 0 END) AS m0" in c.sql
    assert "GROUP BY g0, g1" in c.sql
    assert c.group_columns == ["applied_at_month", "expected_rate"]
    assert c.params == ["application", "top10", "top30"]


def test_measure_where_applies_to_every_aggregate(demo):
    # regression: `where` used to be honoured only by count_if, so these two came out identical
    spec = {
        "entity_type": "application", "mode": "aggregate",
        "group_by": [{"field": "applied_at", "bucket": "month"}],
        "measures": [
            {"name": "days_top10", "agg": "avg_days_between", "field": "applied_at", "field_end": "viewed_at",
             "where": [{"field": "expected_rate", "op": "eq", "value": "top10"}]},
            {"name": "days_top30", "agg": "avg_days_between", "field": "applied_at", "field_end": "viewed_at",
             "where": [{"field": "expected_rate", "op": "eq", "value": "top30"}]},
            {"name": "n_top30", "agg": "count", "where": [{"field": "expected_rate", "op": "eq", "value": "top30"}]},
        ],
    }
    ans = keeper(demo)[0].ask("Q", spec=spec, now=NOW)
    sep = next(g["measures"] for g in ans.result["groups"] if g["group"]["applied_at_month"] == "2026-09")
    # Sept top10 viewed after 1,2,1 days; top30 after 1,2,1,1 days
    assert sep == {"days_top10": 1.33, "days_top30": 1.25, "n_top30": 6}
    octo = next(g["measures"] for g in ans.result["groups"] if g["group"]["applied_at_month"] == "2026-10")
    assert octo["days_top30"] is None and octo["n_top30"] == 9


def test_compile_list_and_not_in(demo):
    spec = QuerySpec.model_validate({
        "entity_type": "application", "mode": "list", "limit": 5, "order_by": "applied_at", "descending": True,
        "filters": [{"field": "status", "op": "not_in", "value": ["rejected", "withdrawn"]}]})
    c = compile_spec(spec, demo.schemas.get("application"), NOW)
    assert "(f_status IS NULL OR f_status NOT IN (?, ?))" in c.sql
    assert c.sql.endswith("ORDER BY f_applied_at DESC, id LIMIT ?") and c.params[-1] == 5


@pytest.mark.parametrize(
    "token,expected",
    [("@today", "2026-10-03"), ("@today-1d", "2026-10-02"), ("@this_month_start", "2026-10-01"),
     ("@last_month_start", "2026-09-01"), ("@this_week_start", "2026-09-28"), ("@today-1m", "2026-09-03"),
     ("@this_year_start", "2026-01-01")],
)
def test_relative_dates_in_user_timezone(token, expected):
    assert resolve_relative(token, NOW) == expected


def test_relative_date_uses_local_day_not_utc():
    # 00:30 KST on 10/3 is still 10/2 in UTC; "today" must be 10/3
    early = datetime.fromisoformat("2026-10-03T00:30:00+09:00")
    assert resolve_relative("@today", early) == "2026-10-03"


# ---- ③ answers -----------------------------------------------------------------------------

def test_aggregate_answer_has_sample_size_and_incomplete_warnings(demo):
    k, _ = keeper(demo)
    ans = k.ask("Q4", spec=Q4_SPEC, now=NOW)
    assert ans.status == "answered"
    groups = {(g["group"]["applied_at_month"], g["group"]["expected_rate"]): (g["measures"]["viewed"], g["n"])
              for g in ans.result["groups"]}
    assert groups == {("2026-09", "top10"): (3, 6), ("2026-09", "top30"): (4, 6),
                      ("2026-10", "top10"): (3, 4), ("2026-10", "top30"): (0, 9)}
    text = " ".join(ans.warnings)
    assert "Small sample" in text and "n=4" in text
    assert "incomplete period" in text
    assert "outcome may not be known yet" in text


def test_count_answer_has_evidence_and_respects_corrections_and_retractions(demo):
    k, _ = keeper(demo)
    ans = k.ask("10/2 사람인 지원 몇 곳?", now=NOW)
    assert ans.result["total"] == 13
    assert "app_o14" in ans.evidence  # corrected from 10/1 to 10/2
    assert "app_o15" not in ans.evidence  # retracted duplicate
    assert len(ans.evidence) == 13


def test_relative_date_resolution_is_shown(demo):
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "applied_at", "op": "eq", "value": "@today-1d"}]}
    k, _ = keeper(demo)
    ans = k.ask("어제 지원 몇 곳?", spec=spec, now=NOW)
    assert ans.resolved["dates"] == {"@today-1d": "2026-10-02"}
    assert any("@today-1d = 2026-10-02" in w for w in ans.warnings)


def test_no_result_warning(demo):
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "platform", "op": "eq", "value": "jobkorea"}]}
    ans = keeper(demo)[0].ask("잡코리아 지원 몇 곳?", spec=spec, now=NOW)
    assert ans.result["total"] == 0 and any("No matching records" in w for w in ans.warnings)


# ---- fast path ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,expected",
    [("10/2 사람인 지원 몇 곳?", 13), ("10월 2일 사람인에 지원한 곳 몇 개?", 13),
     ("어제 원티드 지원 몇 건?", 2), ("2026-09-10 사람인 지원 몇 곳", 3)],
)
def test_structured_questions_skip_the_llm(demo, question, expected):
    k, llm = keeper(demo)  # no scripted responses: any LLM call would fail
    ans = k.ask(question, now=NOW)
    assert ans.status == "answered", ans.text
    assert ans.result["total"] == expected
    assert llm.calls == [] and ans.trace["path"] == "fast" and ans.trace["totals"]["llm_calls"] == 0


def test_entity_id_lookup_skips_the_llm(demo):
    k, llm = keeper(demo)
    ans = k.ask("app_o02", now=NOW)
    assert ans.result["entity"]["company_id"] == "co_gasangtech" and llm.calls == []


@pytest.mark.parametrize(
    "question",
    ["열람됐는데 결과 없는 곳은?", "사람인 지원 중 연봉 높은 곳 몇 개?", "지난달 대비 지원 몇 곳 늘었나?"],
)
def test_fast_path_declines_what_it_cannot_fully_explain(demo, question):
    assert parse_simple(question, schemas_of(demo), NOW) is None


def test_trace_records_stages(demo):
    ans = keeper(demo)[0].ask("10/2 사람인 지원 몇 곳?", now=NOW)
    assert {"interpret", "resolve", "compile", "execute", "answer"} <= set(ans.trace["stages_ms"])
    assert ans.trace["totals"]["total_ms"] > 0


# ---- wider fast path ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,total,filters",
    [
        ("Gasang Tech 예전에 지원한 적 있나?", 1, [("company_id", "in", ["co_gasangtech"])]),
        ("(주)가상테크 지원 몇 곳?", 1, [("company_id", "in", ["co_gasangtech"])]),
        ("Sample Labs 지원 몇 번?", 1, [("company_id", "in", ["co_samplelabs"])]),
        ("열람된 곳 몇 개?", 7, [("status", "eq", "viewed")]),  # type inferred from the status label
        ("사람인 불합격 목록", 2, [("platform", "eq", "saramin"), ("status", "eq", "rejected")]),
    ],
)
def test_wider_fast_path(demo, question, total, filters):
    k, llm = keeper(demo)
    ans = k.ask(question, now=NOW)
    assert ans.status == "answered" and ans.trace["path"] == "fast" and llm.calls == []
    assert ans.result["total"] == total
    assert [(f["field"], f["op"], f["value"]) for f in ans.spec["filters"]] == filters


@pytest.mark.parametrize(
    "question",
    [
        "오늘 열람 몇 건?",  # a date + a status: is it the view date or the application date? -> LLM
        "가상텍 지원한 적 있나?",  # only a similar name: never matched by the fast path
        "가상테크 가상미디어 지원 몇 곳?",  # two names
    ],
)
def test_fast_path_hands_uncertain_questions_to_the_llm(demo, question):
    k = keeper(demo)[0]
    assert parse_simple(question, schemas_of(demo), NOW, resolve_name=k.pipeline._certain_name) is None


# ---- plan cache -----------------------------------------------------------------------------

def test_same_pattern_reuses_the_plan_without_llm(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import seed

    led = Ledger.open(tmp_path / "c.db")
    seed(led)
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "applied_at", "op": "eq", "value": "2026-10-02"},
                        {"field": "status", "op": "eq", "value": "viewed"}],
            "interpretation": "applications on 2026-10-02 that were viewed"}
    k, llm = keeper(led, query_out(spec))
    first = k.ask("10/2 지원 중에 회사가 열어본 거 몇 개?", now=NOW)
    assert first.trace["path"] == "llm" and first.result["total"] == 2
    second = k.ask("9/10 지원 중에 회사가 열어본 거 몇 개?", now=NOW)  # same pattern, other date
    assert second.trace["path"] == "cache" and len(llm.calls) == 1
    assert second.spec["filters"][0]["value"] == "2026-09-10"
    assert second.result["total"] == 2  # app_s02, app_s07
    assert "2026-09-10" in second.spec["interpretation"]
    led.close()


def test_cache_not_used_when_a_slot_is_not_in_the_spec(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import seed
    from mnemento.keeper.query.cache import PlanCache

    led = Ledger.open(tmp_path / "c2.db")
    seed(led)
    spec = QuerySpec.model_validate({"entity_type": "application", "mode": "count"})
    cache = PlanCache(led.storage)
    # the question has a date but the spec ignores it: caching would answer every date the same
    assert cache.store("10/2 지원 전체 몇 개?", spec, schemas_of(led), NOW) is False
    assert cache.lookup("9/30 지원 전체 몇 개?", schemas_of(led), NOW) is None
    led.close()


def test_cache_invalidated_by_schema_version(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import seed
    from mnemento.keeper.query.cache import PlanCache

    led = Ledger.open(tmp_path / "c3.db")
    seed(led)
    spec = QuerySpec.model_validate({"entity_type": "application", "mode": "count", "filters": [
        {"field": "applied_at", "op": "eq", "value": "2026-10-02"}]})
    cache = PlanCache(led.storage)
    assert cache.store("10/2 들어간 거 몇 개?", spec, schemas_of(led), NOW)
    assert cache.lookup("10/1 들어간 거 몇 개?", schemas_of(led), NOW) is not None
    d = led.schemas.get("application").to_dict()
    d["fields"]["note2"] = {"type": "string", "description": "new optional field"}
    led.schemas.bump(d)
    assert cache.lookup("10/1 들어간 거 몇 개?", schemas_of(led), NOW) is None
    led.close()


# ---- events source (Q5/Q6 shapes) -------------------------------------------------------------

def test_events_source_lists_corrections_with_evidence(demo):
    spec = {"source": "events", "entity_type": "application", "mode": "list",
            "filters": [{"field": "kind", "op": "in", "value": ["corrected", "retracted"]}]}
    ans = keeper(demo)[0].ask("정정하거나 취소한 기록 있나?", spec=spec, now=NOW)
    kinds = {(r["entity_id"], r["kind"]) for r in ans.result["rows"]}
    assert kinds == {("app_o14", "corrected"), ("app_o15", "retracted")}
    assert "application-complete mail is dated 10/2" in ans.text and "FROM events" in ans.sql


def test_events_date_filter_uses_local_day(demo):
    spec = {"source": "events", "entity_type": "application", "mode": "count",
            "filters": [{"field": "at", "op": "eq", "value": "2026-10-03"}]}
    ans = keeper(demo)[0].ask("오늘 기록된 사건 몇 개?", spec=spec, now=NOW)
    # KST day 10/3 = UTC 10/2 15:00 .. 10/3 15:00
    assert ans.params[1:] == ["2026-10-02T15:00:00.000000Z", "2026-10-03T15:00:00.000000Z"]
    assert ans.result["total"] == 4  # app_o03 viewed (status+update), o14 corrected, o15 retracted


def test_events_source_rejects_entity_fields(demo):
    spec = QuerySpec.model_validate({"source": "events", "entity_type": "application", "mode": "count",
                                     "filters": [{"field": "status", "op": "eq", "value": "viewed"}]})
    assert any("unknown field 'status'" in e for e in validate_spec(spec, schemas_of(demo)))


def test_hours_between_events(demo):
    spec = {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "status"}],
            "filters": [{"field": "viewed_at", "op": "exists"}],
            "measures": [{"name": "hours_to_view", "agg": "avg_hours_between_events",
                          "event_from": {"kind": "created"}, "event_to": {"kind": "status_changed", "to": "viewed"}}]}
    ans = keeper(demo)[0].ask("열람까지 몇 시간?", spec=spec, now=NOW)
    by_status = {g["group"]["status"]: g["measures"]["hours_to_view"] for g in ans.result["groups"]}
    # s03: 09-12 10:00 -> 09-13 17:00 = 31h; s01 31h & s08 55h -> 43h
    assert by_status["passed"] == 31.0 and by_status["rejected"] == 43.0


def test_hours_between_events_rejects_unknown_status(demo):
    spec = QuerySpec.model_validate({
        "entity_type": "application", "mode": "aggregate",
        "measures": [{"name": "h", "agg": "avg_hours_between_events", "event_from": {"kind": "created"},
                      "event_to": {"kind": "status_changed", "to": "opened"}}]})
    assert any("'opened' not allowed" in e for e in validate_spec(spec, schemas_of(demo)))


def test_free_text_filter_suggests_structured_field(demo):
    spec = {"entity_type": "application", "mode": "list",
            "filters": [{"field": "reason", "op": "contains", "value": "서치펌"}]}
    ans = keeper(demo)[0].ask("서치펌이라 패스한 곳?", spec=spec, now=NOW)
    assert any("free text in 'reason'" in w and "structured field" in w for w in ans.warnings)


# ---- narration (optional, off by default) ----------------------------------------------------

def test_narration_sees_only_aggregates(demo):
    k, llm = keeper(demo, {"answer": "9월엔 30%가, 10월엔 10%가 더 많이 열람됐어요."})
    ans = k.ask("Q4 narrated", spec=Q4_SPEC, now=NOW, narrate=True)
    assert ans.text.startswith("9월엔") and ans.trace["totals"]["llm_calls"] == 1
    payload = llm.calls[0]["prompt"]
    assert '"n": 9' in payload and "Small sample" in payload
    assert "app_o0" not in payload and "co_" not in payload  # no evidence ids or documents


def test_narration_off_by_default(demo):
    k, llm = keeper(demo)
    ans = k.ask("Q4 plain", spec=Q4_SPEC, now=NOW)
    assert llm.calls == [] and ans.text.startswith("Interpretation:")


# ---- reached: history, not current state (ADR-0012) -----------------------------------------

def test_reached_counts_records_that_moved_on(demo):
    # demo: viewed ever = s01(rejected) s02 s03(passed) s07 s08(rejected) s09 s10 o01 o02 o03 -> 10
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "status", "op": "reached", "value": "viewed"}]}
    ans = keeper(demo)[0].ask("열람된 적 있는 지원 몇 개?", spec=spec, now=NOW)
    assert ans.result["total"] == 10
    assert {"app_s01", "app_s03", "app_s08"} <= set(ans.evidence)  # moved on after viewing
    current = {**spec, "filters": [{"field": "status", "op": "eq", "value": "viewed"}]}
    assert keeper(demo)[0].ask("지금 열람 상태 몇 개?", spec=current, now=NOW).result["total"] == 7


def test_reached_follows_implies_and_corrections(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import SCHEMA_DIR

    led = Ledger.open(tmp_path / "r.db")
    led.schemas.load_dir(SCHEMA_DIR)
    t = "2026-10-01T10:00:00+09:00"
    doc = {"company_id": "co_x", "platform": "saramin", "status": "applied", "applied_at": "2026-10-01"}
    led.record_event("a1", "created", doc, t, "a", entity_type="application")
    led.record_event("a1", "status_changed", {"to": "passed"}, "2026-10-02T10:00:00+09:00", "a")  # no viewed event
    led.record_event("a2", "created", doc, t, "a", entity_type="application")
    wrong = led.record_event("a2", "status_changed", {"to": "viewed"}, "2026-10-02T10:00:00+09:00", "a")
    led.record_event("a2", "retracted", {"target": wrong.id}, "2026-10-03T10:00:00+09:00", "a", "wrong record")
    k = Keeper(led, ScriptedLLM())
    spec = {"entity_type": "application", "mode": "list",
            "filters": [{"field": "status", "op": "reached", "value": "viewed"}]}
    ans = k.ask("열람된 지원?", spec=spec, now=NOW)
    assert ans.evidence == ["a1"]  # passed implies viewed; a2's view was retracted
    assert led.get_entity("a2").reached == ["applied"]
    led.close()


def test_reached_validation(demo):
    bad = QuerySpec.model_validate({"entity_type": "application", "mode": "count",
                                    "filters": [{"field": "platform", "op": "reached", "value": "saramin"}]})
    assert any("only works on the status field" in e for e in validate_spec(bad, schemas_of(demo)))
    bad = QuerySpec.model_validate({"entity_type": "application", "mode": "count",
                                    "filters": [{"field": "status", "op": "reached", "value": "opened"}]})
    assert any("not allowed" in e for e in validate_spec(bad, schemas_of(demo)))


def test_interpreter_is_told_about_history(demo):
    good = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "status", "op": "reached", "value": "viewed"}]}
    k, llm = keeper(demo, query_out(good))
    k.ask("열람된 적 있는 지원은 몇 건이야?", now=NOW)
    assert "passed implies it went through viewed" in llm.calls[0]["prompt"]
    assert 'op "reached"' in llm.calls[0]["system"]


def test_old_database_gets_reached_rebuilt(tmp_path):
    import sqlite3

    from mnemento import Ledger
    from mnemento.demo import seed

    path = tmp_path / "old.db"
    led = Ledger.open(path)
    seed(led)
    led.close()
    con = sqlite3.connect(path)  # simulate a database created before ADR-0012
    con.execute("ALTER TABLE entities DROP COLUMN reached")
    con.commit()
    con.close()
    led = Ledger.open(path)
    assert "viewed" in led.get_entity("app_s01").reached  # recomputed from events on open
    led.close()


# ---- having / order_by_event (task 0004 ②) --------------------------------------------------

def test_having_filters_groups(demo):
    spec = {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
            "having": [{"measure": "count", "op": "gte", "value": 3}]}
    ans = keeper(demo)[0].ask("3번 이상 지원한 플랫폼?", spec=spec, now=NOW)
    groups = {g["group"]["platform"]: g["n"] for g in ans.result["groups"]}
    assert groups == {"saramin": 22, "wanted": 5}  # groupby (1) filtered out
    assert ans.sql.endswith("HAVING _n >= ? ORDER BY g0") and ans.params[-1] == 3


def test_having_on_a_measure(demo):
    spec = {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "expected_rate"}],
            "measures": [{"name": "viewed", "agg": "count_if", "where": [{"field": "viewed_at", "op": "exists"}]}],
            "having": [{"measure": "viewed", "op": "gt", "value": 3}]}
    ans = keeper(demo)[0].ask("열람 3건 넘는 등급?", spec=spec, now=NOW)
    assert {g["group"]["expected_rate"] for g in ans.result["groups"]} == {"top10", "top30"}


def test_order_by_event_time(demo):
    spec = {"entity_type": "application", "mode": "list", "limit": 2, "descending": True,
            "filters": [{"field": "status", "op": "reached", "value": "viewed"}],
            "order_by_event": {"kind": "status_changed", "to": "viewed"}}
    ans = keeper(demo)[0].ask("가장 최근에 열람된 두 곳?", spec=spec, now=NOW)
    assert [r["id"] for r in ans.result["rows"]] == ["app_o03", "app_o01"]  # 10/3, then the 10/2 tie by id
    assert "MAX(ev.at_utc)" in ans.sql


@pytest.mark.parametrize(
    "spec,needle",
    [
        ({"entity_type": "application", "mode": "aggregate",
          "having": [{"measure": "count", "op": "gte", "value": 2}]}, "having requires group_by"),
        ({"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
          "having": [{"measure": "nope", "op": "gte", "value": 2}]}, "unknown measure"),
        ({"entity_type": "application", "mode": "count",
          "order_by_event": {"kind": "status_changed", "to": "viewed"}}, "list mode"),
        ({"entity_type": "application", "mode": "list",
          "order_by_event": {"kind": "status_changed", "to": "opened"}}, "not allowed"),
    ],
)
def test_having_and_event_order_validation(demo, spec, needle):
    errs = validate_spec(QuerySpec.model_validate(spec), schemas_of(demo))
    assert any(needle in e for e in errs), errs


def test_measure_names_may_be_korean(demo):
    # regression (v1-regression H3): the model named measures in Korean and validation rejected them
    spec = {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
            "measures": [{"name": "지원수", "agg": "count"}], "having": [{"measure": "지원수", "op": "gte", "value": 3}]}
    ans = keeper(demo)[0].ask("두 번 이상?", spec=spec, now=NOW)
    assert ans.status == "answered" and {g["group"]["platform"] for g in ans.result["groups"]} == {"saramin", "wanted"}
    assert "지원수" not in ans.sql  # names never reach SQL
    bad = QuerySpec.model_validate({"entity_type": "application", "mode": "aggregate",
                                    "measures": [{"name": "x; DROP", "agg": "count"}]})
    assert any("short word" in e for e in validate_spec(bad, schemas_of(demo)))



# ---- task 0005 (v1.1) ---------------------------------------------------------------------------

def test_schema_selection_includes_referencing_types(demo):
    # regression (v2 D7): a question naming only postings must still see the application records
    from mnemento.keeper.query.interpret import select_schemas

    names = [s.name for s in select_schemas("서치펌 공고로 확인돼 패스한 곳은?", schemas_of(demo))]
    assert "posting" in names and "application" in names


def test_event_kinds_are_described_in_the_dictionary(demo):
    from mnemento.keeper.query.interpret import render_dictionary

    text = render_dictionary([demo.schemas.get("application")], {})
    assert "event log (QuerySpec source=events" in text
    assert "corrected [정정/바로잡음/잘못 기록]" in text and "retracted [무효/철회/취소 처리/중복]" in text


@pytest.mark.parametrize(
    "token,expected",
    [("@last_full_week_start(2026-11)", "2026-11-23"), ("@last_full_week_end(2026-11)", "2026-11-29"),
     ("@last_full_week_start(2026-05)", "2026-05-25"), ("@last_full_week_end(2026-05)", "2026-05-31"),
     ("@month_start(2026-02)", "2026-02-01"), ("@month_end(2028-02)", "2028-02-29")],
)
def test_month_tokens(token, expected):
    # Nov 2026 ends on a Monday -> its last full Mon-Sun week is 23-29; May 2026 ends on a Sunday
    assert resolve_relative(token, NOW) == expected


def test_month_tokens_validate_and_compile(demo):
    spec = QuerySpec.model_validate({"entity_type": "application", "mode": "count", "filters": [
        {"field": "applied_at", "op": "gte", "value": "@last_full_week_start(2026-09)"},
        {"field": "applied_at", "op": "lte", "value": "@last_full_week_end(2026-09)"}]})
    assert validate_spec(spec, schemas_of(demo)) == []
    c = compile_spec(spec, demo.schemas.get("application"), NOW)
    assert c.params[1:] == ["2026-09-21", "2026-09-27"]
    bad = QuerySpec.model_validate({"entity_type": "application", "mode": "count", "filters": [
        {"field": "applied_at", "op": "eq", "value": "@last_full_week(2026-09)"}]})
    assert validate_spec(bad, schemas_of(demo))


def test_interpreter_rules_for_event_order_weeks_and_kinds(demo):
    from mnemento.keeper.query.interpret import SYSTEM_PROMPT

    assert "order_by_event" in SYSTEM_PROMPT and "ties records of the same day" in SYSTEM_PROMPT
    assert "@last_full_week_start(YYYY-MM)" in SYSTEM_PROMPT
    assert "corrections (corrected), retractions (retracted)" in SYSTEM_PROMPT


def test_event_counts_report_the_records(demo):
    spec = {"source": "events", "entity_type": "application", "mode": "count",
            "filters": [{"field": "kind", "op": "eq", "value": "retracted"}]}
    ans = keeper(demo)[0].ask("무효 처리한 기록?", spec=spec, now=NOW)
    assert ans.result["groups"][0]["entity_ids"] == ["app_o15"]



# ---- v1.2 (task 0006) -------------------------------------------------------------------------

def test_lists_and_groups_show_referenced_names(demo):
    spec = {"entity_type": "application", "mode": "list", "list_fields": ["company_id", "status"],
            "filters": [{"field": "company_id", "op": "eq", "value": "co_gasangtech"}]}
    ans = keeper(demo)[0].ask("가상테크 지원 목록", spec=spec, now=NOW)
    assert "company_id=co_gasangtech ((주)가상테크)" in ans.text
    assert ans.result["labels"] == {"co_gasangtech": "(주)가상테크"}
    grouped = {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "company_id"}],
               "having": [{"measure": "count", "op": "gte", "value": 1}],
               "filters": [{"field": "company_id", "op": "in", "value": ["co_gasangtech", "co_samplelabs"]}]}
    text = keeper(demo)[0].ask("회사별", spec=grouped, now=NOW).text
    assert "company_id=co_samplelabs (샘플랩스 주식회사)" in text


def _dated_ledger(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import SCHEMA_DIR

    led = Ledger.open(tmp_path / "p.db")
    led.schemas.load_dir(SCHEMA_DIR)
    base = {"company_id": "co_x", "platform": "saramin", "status": "applied"}
    # exact: applied 10/1 10:00, viewed 10/1 13:00
    led.record_event("a_exact", "created", {**base, "applied_at": "2026-10-01"}, "2026-10-01T10:00:00+09:00", "a",
                     entity_type="application")
    led.record_event("a_exact", "status_changed", {"to": "viewed"}, "2026-10-01T13:00:00+09:00", "a")
    # date only: viewed "on 10/2", time unknown
    led.record_event("a_date", "created", {**base, "applied_at": "2026-10-01"}, "2026-10-01T00:00:00+09:00", "a",
                     entity_type="application", at_precision="date")
    led.record_event("a_date", "status_changed", {"to": "viewed"}, "2026-10-02T00:00:00+09:00", "a",
                     at_precision="date")
    # application date never recorded
    led.record_event("a_none", "created", base, "2026-10-03T09:00:00+09:00", "a", entity_type="application",
                     at_precision="unknown")
    return led


def test_unknown_application_date_is_allowed_and_reported(tmp_path):
    led = _dated_ledger(tmp_path)
    assert "applied_at" not in led.get_entity("a_none").doc
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "applied_at", "op": "eq", "value": "2026-10-01"}]}
    ans = Keeper(led, ScriptedLLM()).ask("10/1 지원?", spec=spec, now=NOW)
    assert ans.result["total"] == 2
    assert any("1 application record(s) have no applied_at" in w for w in ans.warnings)
    led.close()


def test_event_time_measures_use_exact_times_only(tmp_path):
    led = _dated_ledger(tmp_path)
    k = Keeper(led, ScriptedLLM())
    hours = {"entity_type": "application", "mode": "aggregate",
             "measures": [{"name": "h", "agg": "avg_hours_between_events", "event_from": {"kind": "created"},
                           "event_to": {"kind": "status_changed", "to": "viewed"}}]}
    ans = k.ask("열람까지 몇 시간?", spec=hours, now=NOW)
    assert ans.result["groups"][0]["measures"]["h"] == 3.0  # a_date (00:00 -> 00:00 next day) is not counted
    assert any("without an exact time" in w for w in ans.warnings)
    order = {"entity_type": "application", "mode": "list", "descending": True,
             "filters": [{"field": "status", "op": "eq", "value": "viewed"}],
             "order_by_event": {"kind": "status_changed", "to": "viewed"}}
    rows = k.ask("최근 열람 순", spec=order, now=NOW).result["rows"]
    assert [r["id"] for r in rows] == ["a_exact", "a_date"]  # inexact last, not first
    assert [e.at_precision for e in led.history("a_date")] == ["date", "date"]
    led.close()


def test_at_precision_validation_and_old_databases(tmp_path):
    import sqlite3

    from mnemento.errors import InvalidEventError

    led = _dated_ledger(tmp_path)
    with pytest.raises(InvalidEventError):
        led.record_event("a_exact", "updated", {"reason": "x"}, "2026-10-04T10:00:00+09:00", "a", at_precision="hour")
    led.close()
    con = sqlite3.connect(tmp_path / "p.db")  # a database from before ADR-0013
    con.execute("ALTER TABLE events DROP COLUMN at_precision")
    con.commit()
    con.close()
    from mnemento import Ledger

    led = Ledger.open(tmp_path / "p.db")
    assert {e.at_precision for e in led.history("a_date")} == {"time"}  # old events: exact by definition
    led.close()


def test_record_tool_passes_at_precision(tmp_path):
    led = _dated_ledger(tmp_path)
    k = Keeper(led, ScriptedLLM())
    res = k.record({"entity_type": "application", "kind": "status_changed", "entity_id": "a_none",
                    "payload": {"to": "viewed"}, "at": "2026-10-03T00:00:00+09:00", "at_precision": "date"},
                   by="agent_a")
    assert res.status == "recorded" and led.history("a_none")[-1].at_precision == "date"
    led.close()



def test_list_without_fields_shows_who_and_status(demo):
    # dogfooding: the interpreter often leaves list_fields empty; the answer must still be readable
    spec = {"entity_type": "application", "mode": "list",
            "filters": [{"field": "company_id", "op": "eq", "value": "co_gasangtech"}]}
    text = keeper(demo)[0].ask("가상테크 지원", spec=spec, now=NOW).text
    assert "- app_o02 (company_id=co_gasangtech ((주)가상테크), status=viewed)" in text



# ---- v1.3: drafts are queryable at once (ADR-0014) ---------------------------------------------------

def test_drafts_are_queryable_with_missing_warning(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import seed

    led = Ledger.open(tmp_path / "d.db")
    seed(led)
    k = Keeper(led, ScriptedLLM())
    k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o05", "payload": {"applicants": 25}},
             by="a")
    k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o06", "payload": {"applicants": 3}},
             by="a")
    k.record({"entity_type": "application", "kind": "created", "entity_id": "app_rm", "at": "2026-10-03T09:00:00+09:00",
              "payload": {"company_id": "co_v20", "platform": "remember", "status": "applied",
                          "applied_at": "2026-10-03"}}, by="a")
    spec = {"entity_type": "application", "mode": "count",
            "filters": [{"field": "applicants", "op": "gte", "value": 10}]}
    ans = k.ask("지원자 10명 이상?", spec=spec, now=NOW)
    assert ans.status == "answered" and ans.evidence == ["app_o05"]
    assert any("have no applicants (unregistered draft field)" in w for w in ans.warnings)
    assert "json_extract(doc, '$.applicants')" in ans.sql  # no index for a draft: read from the document
    ans = k.ask("리멤버 지원?", spec={"entity_type": "application", "mode": "count",
                                    "filters": [{"field": "platform", "op": "eq", "value": "remember"}]}, now=NOW)
    assert ans.evidence == ["app_rm"]
    bad = k.ask("x", spec={"entity_type": "application", "mode": "count",
                           "filters": [{"field": "platform", "op": "eq", "value": "linkedin"}]}, now=NOW)
    assert bad.status == "error"  # values never seen are still rejected (no hallucinated values)
    led.close()


def test_interpreter_sees_drafts_marked(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import seed
    from mnemento.keeper.drafts import effective_schema
    from mnemento.keeper.query.interpret import render_dictionary

    led = Ledger.open(tmp_path / "d.db")
    seed(led)
    before = render_dictionary([effective_schema(led, "application")], {})
    assert "UNREGISTERED" not in before and "draft" not in before  # no drafts: same dictionary as before
    k = Keeper(led, ScriptedLLM())
    k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o05", "payload": {"applicants": 25}},
             by="a")
    k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o06",
              "payload": {"platform": "remember"}}, by="a")
    text = render_dictionary([effective_schema(led, "application")], {})
    assert "- applicants (integer)" in text and "UNREGISTERED (draft)" in text
    assert "unregistered values in use (draft): remember" in text
    led.close()
