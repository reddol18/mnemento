"""Composable measures and conditions (ADR-0029): aggregates over a span, one unit table, list values everywhere,
precision checked once, the asked shape, and older names rewritten on read. All LLM calls go to ScriptedLLM."""

from datetime import date, datetime
from statistics import median

import pytest

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.interpret import SYSTEM_PROMPT, InterpretOutput
from mnemento.keeper.query.spec import QuerySpec, validate_spec

NOW = datetime.fromisoformat(DEMO_NOW)
DATES = {"start": {"field": "applied_at"}, "end": {"field": "viewed_at"}}
EVENTS = {"start": {"event": {"kind": "created"}}, "end": {"event": {"kind": "status_changed", "to": "viewed"}}}


@pytest.fixture(scope="module")
def demo():
    led = open_demo()
    yield led
    led.close()


def ask(led, spec):
    return Keeper(led, ScriptedLLM()).ask("q", spec=spec, now=NOW)


def agg(*measures, **extra):
    return {"entity_type": "application", "mode": "aggregate", "measures": list(measures), **extra}


def schemas_of(led):
    return {n: led.schemas.get(n) for n in led.schemas.names()}


def days_to_view(led):
    return [(date.fromisoformat(e.doc["viewed_at"]) - date.fromisoformat(e.doc["applied_at"])).days
            for e in led.find("application") if e.doc.get("viewed_at") and e.doc.get("applied_at")]


def test_every_aggregate_over_a_span(demo):
    d = days_to_view(demo)
    ans = ask(demo, agg(*[{"name": a, "agg": a, "span": DATES, "unit": "days"}
                          for a in ("sum", "avg", "median", "min", "max")]))
    assert ans.status == "answered", ans.text
    m = ans.result["groups"][0]["measures"]
    assert m["sum"] == sum(d) and m["min"] == min(d) and m["max"] == max(d)
    assert abs(m["avg"] - sum(d) / len(d)) < 0.01 and m["median"] == median(d)


def test_one_unit_table_for_spans_and_elapsed(demo):
    ans = ask(demo, agg({"name": "d", "agg": "sum", "span": DATES, "unit": "days"},
                        {"name": "w", "agg": "sum", "span": DATES, "unit": "weeks"},
                        {"name": "h", "agg": "avg", "span": EVENTS, "unit": "hours"},
                        {"name": "hd", "agg": "avg", "span": EVENTS, "unit": "days"}))
    m = ans.result["groups"][0]["measures"]
    assert abs(m["w"] - m["d"] / 7) < 0.01 and abs(m["hd"] - m["h"] / 24) < 0.01  # results are rounded
    over_a_week = ask(demo, {"entity_type": "application", "mode": "count", "elapsed": [
        {**DATES, "unit": "weeks", "op": "gt", "value": 1}]})
    assert over_a_week.result["total"] == sum(1 for x in days_to_view(demo) if x > 7)


def test_older_names_are_rewritten_on_read():
    spec = QuerySpec.model_validate(agg(
        {"name": "a", "agg": "avg_days_between", "field": "applied_at", "field_end": "viewed_at"},
        {"name": "b", "agg": "avg_hours_between_events", "unit": "days",
         "event_from": {"kind": "created"}, "event_to": {"kind": "status_changed", "to": "viewed"}},
        {"name": "c", "agg": "count_if", "where": [{"field": "status", "op": "in", "value": ["passed", "rejected"]}]},
        filters=[{"field": "status", "op": "not_in", "value": ["withdrawn"]}]))
    a, b, c = spec.measures
    assert (a.agg, a.unit, a.span.start.field, a.span.end.field) == ("avg", "days", "applied_at", "viewed_at")
    assert (b.agg, b.unit, b.span.end.event.to) == ("avg", "days", "viewed")
    assert c.agg == "count" and c.where[0].op == "eq" and spec.filters[0].op == "ne"
    # the model is shown one form only
    schema = str(InterpretOutput.model_json_schema())
    for old in ("count_if", "avg_days_between", "avg_hours_between_events", "'not_in'"):
        assert old not in schema


def test_list_values_eq_ne_and_reached(demo):
    apps = list(demo.find("application"))
    done = {"passed", "rejected", "withdrawn"}
    not_done = ask(demo, {"entity_type": "application", "mode": "count",
                          "filters": [{"field": "status", "op": "ne", "value": sorted(done)}]})
    assert not_done.result["total"] == sum(1 for e in apps if e.doc.get("status") not in done)
    assert "NOT IN" in not_done.sql
    either = ask(demo, {"entity_type": "application", "mode": "count",
                        "filters": [{"field": "status", "op": "eq", "value": ["passed", "rejected"]}]})
    assert either.result["total"] == sum(1 for e in apps if e.doc.get("status") in ("passed", "rejected"))
    reached = ask(demo, {"entity_type": "application", "mode": "count",
                         "filters": [{"field": "status", "op": "reached", "value": ["passed", "rejected"]}]})
    assert reached.status == "answered", reached.text
    assert reached.result["total"] >= either.result["total"]
    single = ask(demo, {"entity_type": "application", "mode": "count",
                        "filters": [{"field": "applied_at", "op": "gt", "value": ["2026-10-01"]}]})
    assert single.status == "error" and "takes a single value" in single.text


@pytest.mark.parametrize("measure,needle", [
    ({"name": "x", "agg": "avg", "span": DATES, "unit": "hours"}, "hold only the day"),
    ({"name": "x", "agg": "avg", "span": {"start": {"field": "applied_at"},
                                          "end": {"event": {"kind": "status_changed", "to": "viewed"}}},
      "unit": "days"}, "do not mix"),
    ({"name": "x", "agg": "avg", "span": DATES}, "needs the unit"),
    ({"name": "x", "agg": "count", "span": DATES, "unit": "days"}, "count counts records"),
    ({"name": "x", "agg": "sum", "field": "applied_at", "span": DATES, "unit": "days"}, "one of field, span"),
    ({"name": "x", "agg": "avg", "unit": "days", "field": "expected_rate"}, "unit is for a span"),
    ({"name": "x", "agg": "median"}, "or give a span"),
])
def test_span_validation(demo, measure, needle):
    errs = validate_spec(QuerySpec.model_validate(agg(measure)), schemas_of(demo))
    assert any(needle in e for e in errs), errs


def test_the_record_that_took_longest_is_a_list_sorted_by_a_span_value(demo):
    ans = ask(demo, {"entity_type": "application", "mode": "list", "limit": 1, "order_by": "took",
                     "descending": True, "values": [{"name": "took", "span": DATES, "unit": "days"}]})
    assert ans.status == "answered", ans.text
    assert ans.result["rows"][0]["took"] == max(days_to_view(demo))
    as_measure = validate_spec(QuerySpec.model_validate(agg(
        {"name": "x", "agg": "sum", "field": "took"},
        values=[{"name": "took", "span": DATES, "unit": "days"}])), schemas_of(demo))
    assert any("use the measure's span" in e for e in as_measure)
    assert "제일 오래 걸린 지원은?" in SYSTEM_PROMPT and "not a max measure" in SYSTEM_PROMPT


@pytest.mark.parametrize("asks,measures,ok", [
    ({"calc": "sum", "unit": "days"}, [{"name": "n", "agg": "count"}], False),  # unseen v5 Y4: a count for a total
    ({"calc": "sum", "unit": "days"}, [{"name": "s", "agg": "sum", "span": DATES, "unit": "days"}], True),
    ({"calc": "avg", "unit": "weeks"}, [{"name": "a", "agg": "avg", "span": DATES, "unit": "days"}], False),
    ({"calc": "count"}, [{"name": "n", "agg": "count"}], True),
    ({"calc": "other"}, [{"name": "n", "agg": "count"}], True),
    ({"calc": "list"}, [{"name": "n", "agg": "count"}], True),  # dev V3: which companies = groups (with group_by)
])
def test_the_measures_must_give_what_is_asked(demo, asks, measures, ok):
    extra = {"group_by": [{"field": "company_id"}]} if asks["calc"] == "list" else {}
    errs = validate_spec(QuerySpec.model_validate(agg(*measures, asks=asks, **extra)), schemas_of(demo))
    assert (not errs) == ok, errs
    if not ok:
        assert "kind=unsupported" in errs[0]


def test_unsupported_is_said_not_answered_near(demo):
    k = Keeper(demo, ScriptedLLM([{"kind": "unsupported", "unsupported_reason": "공고 마감일과의 차이는 계산할 수 없음"}]))
    ans = k.ask("마감 사흘 전에 낸 지원은?", now=NOW)
    assert ans.status == "unsupported" and "마감일" in ans.text and ans.result is None


def test_the_model_must_write_asks_first(demo):
    seen = {}

    class Spy(ScriptedLLM):
        def complete_json(self, *, system, prompt, schema, stage):
            seen["schema"] = schema
            return super().complete_json(system=system, prompt=prompt, schema=schema, stage=stage)

    Keeper(demo, Spy([{"kind": "clarify", "clarify_question": "?"}])).ask("뭐든", now=NOW)
    assert "asks" in seen["schema"]["$defs"]["QuerySpec"]["required"]


def test_a_record_without_the_event_time_is_left_out_and_counted():
    led = open_demo()
    try:  # imported already viewed: it has the status but no exact time of the view
        led.record_event("app_import1", "created", {"company_id": "co_v23", "platform": "saramin",
                                                     "status": "viewed", "applied_at": "2026-10-05",
                                                     "viewed_at": "2026-10-07"},
                         "2026-10-08T09:00:00+09:00", "test", "import", entity_type="application")
        ans = ask(led, agg({"name": "h", "agg": "avg", "span": EVENTS, "unit": "hours"}))
        assert ans.status == "answered", ans.text
        assert any("1 record(s) reached viewed but have no exact time" in w for w in ans.warnings), ans.warnings
    finally:
        led.close()


def test_median_aggregate_on_the_connection(demo):
    rows = demo.storage.fetch_all("SELECT MEDIAN(x) AS m FROM (SELECT 1 AS x UNION ALL SELECT 4 UNION ALL "
                                  "SELECT NULL UNION ALL SELECT 2 UNION ALL SELECT 10)")
    assert rows[0]["m"] == 3


def test_bench_credits_only_an_honest_unsupported():
    from bench.grade import grade
    from bench.questions import Question

    q = Question("Z1", "unseen6", "마감 사흘 전에 낸 지원은?", "number = count", "unsupported")
    assert grade(q, {"number": None, "unsupported": True})[0]
    assert not grade(q, {"number": 3, "unsupported": False})[0]
