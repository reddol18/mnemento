"""Elapsed-time conditions, vague-word defaults and the schema files of an organized database (ADR-0018).
All LLM calls go to ScriptedLLM (no network)."""

from datetime import date, datetime, timedelta

import pytest

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.interpret import SYSTEM_PROMPT, render_dictionary
from mnemento.keeper.query.spec import QuerySpec, validate_spec
from mnemento.schema.definition import SchemaDef
from mnemento.errors import SchemaDefinitionError

NOW = datetime.fromisoformat(DEMO_NOW)


@pytest.fixture(scope="module")
def demo():
    led = open_demo()
    yield led
    led.close()


def apps(led):
    return [e for e in led.find("application")]


def ask(led, spec):
    return Keeper(led, ScriptedLLM()).ask("q", spec=spec, now=NOW)


def count_spec(*elapsed, filters=()):
    return {"entity_type": "application", "mode": "count", "filters": list(filters), "elapsed": list(elapsed)}


@pytest.mark.parametrize("op,value", [("gt", 0), ("gte", 1), ("lte", 1), ("gt", 1)])
def test_days_between_two_date_fields(demo, op, value):
    def days(e):
        return (date.fromisoformat(e.doc["viewed_at"]) - date.fromisoformat(e.doc["applied_at"])).days

    cmp = {"gt": lambda d: d > value, "gte": lambda d: d >= value, "lte": lambda d: d <= value}[op]
    expected = sorted(e.id for e in apps(demo)
                      if e.doc.get("viewed_at") and e.doc.get("applied_at") and cmp(days(e)))
    ans = ask(demo, count_spec({"start": {"field": "applied_at"}, "end": {"field": "viewed_at"},
                                "unit": "days", "op": op, "value": value}))
    assert ans.status == "answered", ans.text
    assert sorted(ans.evidence) == expected
    assert "julianday" in ans.sql


def test_days_to_today_matches_the_date_filter(demo):
    by_elapsed = ask(demo, count_spec({"start": {"field": "applied_at"}, "unit": "days", "op": "gt",
                                       "value": 20}))
    by_filter = ask(demo, count_spec(filters=[{"field": "applied_at", "op": "lt", "value": "@today-20d"}]))
    cutoff = NOW.date() - timedelta(days=20)
    expected = sorted(e.id for e in apps(demo)
                      if e.doc.get("applied_at") and date.fromisoformat(e.doc["applied_at"]) < cutoff)
    assert sorted(by_elapsed.evidence) == sorted(by_filter.evidence) == expected


def test_hours_between_events(demo):
    ev = {"start": {"event": {"kind": "created"}}, "end": {"event": {"kind": "status_changed", "to": "viewed"}},
          "unit": "hours", "op": "gt", "value": 48}
    ans = ask(demo, count_spec(ev))
    assert ans.status == "answered", ans.text

    def first(e, pred):
        times = [x.at_utc for x in demo.history(e.id) if x.id in e.event_ids and x.at_precision == "time"
                 and pred(x)]
        return min(times) if times else None

    def hours(e):
        a = first(e, lambda x: x.kind == "created")
        b = first(e, lambda x: x.kind == "status_changed" and x.payload.get("to") == "viewed")
        if a is None or b is None:
            return None
        parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
        return (parse(b) - parse(a)).total_seconds() / 3600

    expected = sorted(e.id for e in apps(demo) if (h := hours(e)) is not None and h > 48)
    assert sorted(ans.evidence) == expected


def test_missing_endpoint_is_excluded_and_reported(demo):
    ans = ask(demo, count_spec({"start": {"field": "applied_at"}, "end": {"field": "viewed_at"},
                                "unit": "days", "op": "gte", "value": 0}))
    missing = sum(1 for e in apps(demo) if not e.doc.get("viewed_at"))
    assert missing and ans.result["total"] == len(apps(demo)) - missing - sum(
        1 for e in apps(demo) if e.doc.get("viewed_at") and not e.doc.get("applied_at"))
    assert any(f"{missing} application record(s) have no viewed_at" in w for w in ans.warnings)


@pytest.mark.parametrize(
    "elapsed,needle",
    [
        ({"start": {"field": "applied_at"}, "end": {"event": {"kind": "created"}}, "unit": "days", "op": "gt",
          "value": 1}, "do not mix"),
        ({"start": {"field": "applied_at"}, "unit": "hours", "op": "gt", "value": 1}, "or unit days"),
        ({"start": {"field": "applied_at", "today": True}, "unit": "days", "op": "gt", "value": 1}, "exactly one"),
        ({"start": {"field": "platform"}, "unit": "days", "op": "gt", "value": 1}, "not a date field"),
        ({"start": {"event": {"kind": "status_changed", "to": "ghosted"}}, "unit": "hours", "op": "gt",
          "value": 1}, "not allowed"),
        ({"start": {"field": "applied_at"}, "unit": "days", "op": "gt", "value": -1}, "negative"),
    ],
)
def test_elapsed_validation(demo, elapsed, needle):
    schemas = {n: demo.schemas.get(n) for n in demo.schemas.names()}
    errs = validate_spec(QuerySpec.model_validate(count_spec(elapsed)), schemas)
    assert any(needle in e for e in errs), errs


def test_defaults_used_are_shown_as_warnings(demo):
    spec = {**count_spec({"start": {"field": "applied_at"}, "end": {"field": "viewed_at"}, "unit": "days",
                          "op": "lte", "value": 1}),
            "defaults_used": ["빠르게 = viewed within 1 day of applying"]}
    ans = ask(demo, spec)
    assert ans.warnings[0].startswith("Read with a default: 빠르게 = viewed within 1 day")


def test_interpreter_answers_with_a_default_instead_of_asking(demo):
    spec = {"entity_type": "application", "mode": "aggregate",
            "measures": [{"name": "avg_hours", "agg": "avg_hours_between_events",
                          "event_from": {"kind": "created"}, "event_to": {"kind": "status_changed", "to": "viewed"}}],
            "defaults_used": ["빠르게 = viewed within 1 day of applying"],
            "interpretation": "average hours to the first view"}
    k = Keeper(demo, ScriptedLLM([{"kind": "query", "spec": spec}]))
    ans = k.ask("열람까지 평균 몇 시간? 빠르게 열람된 곳은?", now=NOW)
    assert ans.status == "answered" and any("Read with a default" in w for w in ans.warnings)
    prompt = k.llm.calls[0]["system"] + k.llm.calls[0]["prompt"]
    assert "'빠르게 열람 / quickly viewed' = viewed within 1 day" in prompt


def test_interpreter_rules_cover_elapsed_lengths_and_vague_words():
    for needle in ("condition (\"viewed more than 3 days after applying\", \"took less than a day\"): `elapsed`", "1 month = 30 days", "spec.defaults_used", "never drop the main part"):
        assert needle in SYSTEM_PROMPT


def test_status_dictionary_says_what_no_outcome_means(demo):
    text = render_dictionary([demo.schemas.get("application")], {})
    assert "applied and viewed mean no outcome yet" in text


def test_vague_terms_round_trip_and_validation():
    base = {"name": "note", "version": 1, "description": "a note",
            "fields": {"title": {"type": "string", "description": "title"}}}
    s = SchemaDef.from_dict({**base, "vague_terms": {"최근": "the last 7 days"}})
    assert s.vague_terms == (("최근", "the last 7 days"),)
    assert SchemaDef.from_dict(s.to_dict()) == s and s.with_version(2).vague_terms == s.vague_terms
    with pytest.raises(SchemaDefinitionError):
        SchemaDef.from_dict({**base, "vague_terms": {"최근": ""}})


def test_files_do_not_override_a_user_organized_database(tmp_path):
    """ADR-0018: once the user organized a type (ADR-0014), a differing schema file is not applied."""
    from mnemento import Ledger

    from .conftest import SCHEMA_DIR

    led = Ledger.open(tmp_path / "o.db")
    led.schemas.load_dir(SCHEMA_DIR)
    v = led.schemas.get("application").version
    led.storage.record_schema_change({"entity_type": "application", "version": v, "proposal_id": "p1",
                                      "approved_by": "user", "user_answer": "yes", "applied_at": DEMO_NOW})
    changed = tmp_path / "schemas"
    changed.mkdir()
    for f in SCHEMA_DIR.glob("*.json"):
        (changed / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    p = changed / "application.json"
    p.write_text(p.read_text(encoding="utf-8").replace(f'"version": {v}', f'"version": {v + 1}'), encoding="utf-8")
    led.schemas.load_dir(changed)
    assert led.schemas.get("application").version == v
    assert any("organized by the user" in n for n in led.schemas.load_notes)
    led.close()


def test_relations_round_trip_render_and_validation():
    """ADR-0017 (moved to step 3): relation notes reach the interpreter so one event is counted from one type."""
    base = {"name": "trade", "version": 1, "description": "a trade",
            "fields": {"side": {"type": "string", "description": "buy or sell"}}}
    s = SchemaDef.from_dict({**base, "relations": [{"type": "decision", "note": "count trades from trade"}]})
    assert s.relations == (("decision", "count trades from trade"),)
    assert SchemaDef.from_dict(s.to_dict()) == s and s.with_version(2).relations == s.relations
    assert "related to decision: count trades from trade" in render_dictionary([s], {})
    for bad in ([{"type": "decision"}], [{"type": "Bad Type", "note": "x"}], {"decision": "x"}):
        with pytest.raises(SchemaDefinitionError):
            SchemaDef.from_dict({**base, "relations": bad})


# ---- issues #10/#11: a time condition on one measure; a vague word narrows only its own part --------------------

def test_a_time_condition_narrows_one_measure_only():
    from mnemento.demo import open_demo
    from mnemento.keeper import Keeper, ScriptedLLM

    led = open_demo()
    k = Keeper(led, ScriptedLLM())
    quick = {"start": {"field": "applied_at"}, "end": {"field": "viewed_at"}, "unit": "days", "op": "lte", "value": 1}
    base = {"entity_type": "application", "mode": "aggregate",
            "filters": [{"field": "viewed_at", "op": "exists"}]}
    both = k.ask("q", spec={**base, "measures": [
        {"name": "all_viewed", "agg": "count"},
        {"name": "quick", "agg": "count_if", "elapsed": [quick]},
        {"name": "quick_passed", "agg": "count_if", "elapsed": [quick],
         "where": [{"field": "status", "op": "eq", "value": "passed"}]}]})
    whole = k.ask("q", spec={**base, "elapsed": [quick], "measures": [{"name": "n", "agg": "count"}]})
    m = both.result["groups"][0]["measures"]
    assert m["quick"] == whole.result["total"] and m["all_viewed"] >= m["quick"] >= m["quick_passed"]
    led.close()


def test_the_interpreter_is_told_where_a_vague_reading_goes():
    from mnemento.keeper.query.interpret import SYSTEM_PROMPT

    assert "narrows only the part of the question the word is in" in SYSTEM_PROMPT
    assert "that measure's `elapsed`" in SYSTEM_PROMPT


# ---- issue #16: hours vs days; events with several statuses ------------------------------------------------------

def test_units_on_time_between_measures_and_multi_status_events():
    from mnemento.demo import open_demo
    from mnemento.keeper import Keeper, ScriptedLLM

    led = open_demo()
    k = Keeper(led, ScriptedLLM())
    base = {"entity_type": "application", "mode": "aggregate"}
    dates = {"field": "applied_at", "field_end": "viewed_at"}
    ev = {"event_from": {"kind": "created"}, "event_to": {"kind": "status_changed", "to": "viewed"}}
    a = k.ask("q", spec={**base, "measures": [{"name": "d", "agg": "avg_days_between", "unit": "days", **dates},
                                             {"name": "eh", "agg": "avg_hours_between_events", **ev},
                                             {"name": "ed", "agg": "avg_hours_between_events", "unit": "days", **ev}]})
    m = a.result["groups"][0]["measures"]
    assert m["d"] is not None and abs(m["ed"] - m["eh"] / 24) < 0.01
    hours_from_dates = k.ask("q", spec={**base, "measures": [
        {"name": "h", "agg": "avg_days_between", "unit": "hours", **dates}]})
    assert hours_from_dates.status == "error" and "hold only the day" in hours_from_dates.text
    res = {"event_from": {"kind": "created"},
           "event_to": {"kind": "status_changed", "to": ["passed", "rejected"]}}
    one = k.ask("q", spec={**base, "measures": [
        {"name": "any_result", "agg": "avg_hours_between_events", "unit": "days", **res},
        {"name": "n", "agg": "count_if", "where": [{"field": "status", "op": "in", "value": ["passed", "rejected"]}]}]})
    assert one.status == "answered" and one.result["groups"][0]["measures"]["any_result"] is not None
    led.close()


def test_a_unit_only_on_time_between_measures():
    from mnemento.demo import open_demo
    from mnemento.keeper.query.spec import QuerySpec, validate_spec

    led = open_demo()
    schemas = {n: led.schemas.get(n) for n in led.schemas.names()}
    bad = QuerySpec.model_validate({"entity_type": "application", "mode": "aggregate",
                                    "measures": [{"name": "c", "agg": "count", "unit": "days"}]})
    assert any("unit is for" in e for e in validate_spec(bad, schemas))
    wrong = QuerySpec.model_validate({"entity_type": "application", "mode": "aggregate", "measures": [
        {"name": "x", "agg": "avg_hours_between_events", "event_from": {"kind": "created"},
         "event_to": {"kind": "status_changed", "to": ["passed", "nope"]}}]})
    assert any("'nope' not allowed" in e for e in validate_spec(wrong, schemas))
    led.close()
