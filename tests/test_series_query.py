"""Series questions (ADR-0016): every query feature against answers computed from the generator's rows, on two
fictional domains (weight, daily spending). No LLM: structured QuerySpecs."""

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import pytest

from bench.series_gen import SPENDING_SCHEMA, WEIGHT_SCHEMA, generate
from mnemento import Ledger
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.spec import QuerySpec, validate_spec
from mnemento.series import ingest

NOW = datetime.fromisoformat("2026-09-27T21:00:00+09:00")  # last generated day is 2026-09-27
DATA = generate()
DOMAINS = {  # type, key field, key value, time field, measure
    "weight": ("weight", "person", "가상인", "measured_on", "kg", DATA.weight),
    "spending": ("spending", "category", "식비", "spent_on", "amount", DATA.spending),
}


@pytest.fixture(scope="module")
def k():
    led = Ledger.open(":memory:")
    led.schemas.register(WEIGHT_SCHEMA)
    led.schemas.register(SPENDING_SCHEMA)
    ingest(led, "weight", DATA.weight, by="gen", source="series_gen")
    ingest(led, "spending", DATA.spending, by="gen", source="series_gen")
    keeper = Keeper(led, ScriptedLLM())
    keeper.pipeline.query_log = None
    yield keeper
    led.close()


def ask(k, spec):
    ans = k.ask("q", spec={"source": "series", **spec}, now=NOW)
    assert ans.status == "answered", ans.text
    return ans


def rows_of(domain, lo=None, hi=None):
    t, kf, kv, tf, m, rows = DOMAINS[domain]
    return sorted((r for r in rows if r[kf] == kv and (lo is None or r[tf] >= lo) and (hi is None or r[tf] <= hi)),
                  key=lambda r: r[tf])


def period(tf, lo, hi):
    return [{"field": tf, "op": "gte", "value": lo}, {"field": tf, "op": "lte", "value": hi}]


@pytest.mark.parametrize("domain", DOMAINS)
def test_list_points_in_a_period(k, domain):
    t, kf, kv, tf, m, _ = DOMAINS[domain]
    ans = ask(k, {"entity_type": t, "mode": "list", "limit": 500,
                  "filters": [{"field": kf, "op": "eq", "value": kv}, *period(tf, "2026-03-01", "2026-03-31")]})
    expect = rows_of(domain, "2026-03-01", "2026-03-31")
    assert ans.result["total"] == len(expect)
    assert [r[m] for r in ans.result["rows"]] == [r[m] for r in expect]
    assert ans.evidence[0] == f"{t}:{kv}@{expect[0][tf]}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_monthly_average_and_extremes(k, domain):
    t, kf, kv, tf, m, _ = DOMAINS[domain]
    ans = ask(k, {"entity_type": t, "mode": "aggregate", "filters": [{"field": kf, "op": "eq", "value": kv}],
                  "group_by": [{"field": tf, "bucket": "month"}],
                  "measures": [{"name": "avg_v", "agg": "avg", "field": m}, {"name": "max_v", "agg": "max", "field": m},
                               {"name": "min_v", "agg": "min", "field": m}]})
    by_month = defaultdict(list)
    for r in rows_of(domain):
        by_month[r[tf][:7]].append(r[m])
    got = {g["group"][f"{tf}_month"]: g for g in ans.result["groups"]}
    assert set(got) == set(by_month)
    for mon, vals in by_month.items():
        assert got[mon]["n"] == len(vals)
        assert got[mon]["measures"]["avg_v"] == pytest.approx(statistics.mean(vals), abs=0.01)
        assert (got[mon]["measures"]["max_v"], got[mon]["measures"]["min_v"]) == (max(vals), min(vals))
    assert any("2026-09 is still in progress" in w for w in ans.warnings)


@pytest.mark.parametrize("domain", DOMAINS)
def test_first_last_change_over_a_period_per_key(k, domain):
    t, kf, kv, tf, m, all_rows = DOMAINS[domain]
    ans = ask(k, {"entity_type": t, "mode": "aggregate", "filters": period(tf, "2026-02-01", "2026-05-31"),
                  "group_by": [{"field": kf}],
                  "measures": [{"name": "first_v", "agg": "first", "field": m}, {"name": "last_v", "agg": "last", "field": m},
                               {"name": "chg", "agg": "change", "field": m}, {"name": "chg_pct", "agg": "change_pct", "field": m}]})
    for g in ans.result["groups"]:
        key = g["group"][kf]
        rs = sorted((r for r in all_rows if r[kf] == key and "2026-02-01" <= r[tf] <= "2026-05-31"), key=lambda r: r[tf])
        first, last = rs[0][m], rs[-1][m]
        assert (g["measures"]["first_v"], g["measures"]["last_v"]) == (first, last)
        assert g["measures"]["chg"] == pytest.approx(last - first, abs=0.01)
        assert g["measures"]["chg_pct"] == pytest.approx((last - first) * 100 / first, abs=0.01)


def test_first_skips_empty_values():
    """body_fat_pct is not measured every day: first/last take the first/last day that has it."""
    led = Ledger.open(":memory:")
    led.schemas.register(WEIGHT_SCHEMA)
    ingest(led, "weight", [{"person": "가상인", "measured_on": "2026-07-01", "kg": 70},
                           {"person": "가상인", "measured_on": "2026-07-02", "kg": 70.2, "body_fat_pct": 21.0},
                           {"person": "가상인", "measured_on": "2026-07-03", "kg": 70.4, "body_fat_pct": 22.5},
                           {"person": "가상인", "measured_on": "2026-07-04", "kg": 70.1}], by="t", source="s")
    k = Keeper(led, ScriptedLLM())
    ans = k.ask("q", spec={"source": "series", "entity_type": "weight", "mode": "aggregate",
                           "measures": [{"name": "f", "agg": "first", "field": "body_fat_pct"},
                                        {"name": "l", "agg": "last", "field": "body_fat_pct"}]}, now=NOW)
    assert ans.result["groups"][0]["measures"] == {"f": 21.0, "l": 22.5}
    led.close()


@pytest.mark.parametrize("domain", DOMAINS)
def test_points_below_their_moving_average(k, domain):
    """'Days below the 28-day average' — the window uses the days before the asked period too."""
    t, kf, kv, tf, m, _ = DOMAINS[domain]
    ans = ask(k, {"entity_type": t, "mode": "count",
                  "filters": [{"field": kf, "op": "eq", "value": kv}, *period(tf, "2026-06-01", "2026-06-30")],
                  "window": [{"name": "avg28", "measure": m, "agg": "avg", "size": 28, "unit": "days"}],
                  "compare": [{"measure": m, "op": "lt", "baseline": {"window": "avg28"}}]})
    rows = rows_of(domain)
    hits = 0
    for r in rows:
        if not ("2026-06-01" <= r[tf] <= "2026-06-30"):
            continue
        d = date.fromisoformat(r[tf])
        win = [x[m] for x in rows if d - timedelta(days=27) <= date.fromisoformat(x[tf]) <= d]
        hits += r[m] < statistics.mean(win) - 1e-9
    assert ans.result["total"] == hits > 0


def test_moving_average_by_points_in_a_list(k):
    ans = ask(k, {"entity_type": "weight", "mode": "list", "limit": 500,
                  "filters": [{"field": "person", "op": "eq", "value": "샘플인"}],
                  "window": [{"name": "ma7", "measure": "kg", "agg": "avg", "size": 7, "unit": "points"}],
                  "list_fields": ["measured_on", "kg", "ma7"]})
    rows = sorted((r for r in DATA.weight if r["person"] == "샘플인"), key=lambda r: r["measured_on"])
    for i, got in enumerate(ans.result["rows"]):
        expect = statistics.mean(r["kg"] for r in rows[max(0, i - 6):i + 1])
        assert got["ma7"] == pytest.approx(expect, abs=0.01)


def test_spending_count_if_and_sum_per_category(k):
    ans = ask(k, {"entity_type": "spending", "mode": "aggregate", "filters": period("spent_on", "2026-08-01", "2026-08-31"),
                  "group_by": [{"field": "category"}],
                  "measures": [{"name": "total", "agg": "sum", "field": "amount"},
                               {"name": "big_days", "agg": "count_if", "where": [{"field": "amount", "op": "gte", "value": 30000}]}]})
    for g in ans.result["groups"]:
        rs = [r for r in DATA.spending if r["category"] == g["group"]["category"] and r["spent_on"].startswith("2026-08")]
        assert g["measures"]["total"] == sum(r["amount"] for r in rs)
        assert g["measures"]["big_days"] == sum(r["amount"] >= 30000 for r in rs)


def test_gap_warning_counts_missing_days(k):
    ans = ask(k, {"entity_type": "weight", "mode": "count",
                  "filters": [{"field": "person", "op": "eq", "value": "가상인"}, *period("measured_on", "2026-06-01", "2026-06-30")]})
    present = len(rows_of("weight", "2026-06-01", "2026-06-30"))
    assert present < 30
    assert ans.result["total"] == present
    assert any(f"가상인: {30 - present} of 30 days have no point" in w for w in ans.warnings)


def test_relative_period_tokens(k):
    ans = ask(k, {"entity_type": "spending", "mode": "aggregate",
                  "filters": [{"field": "spent_on", "op": "gte", "value": "@today-6d"}],
                  "measures": [{"name": "total", "agg": "sum", "field": "amount"}]})
    since = (NOW.date() - timedelta(days=6)).isoformat()
    assert ans.result["groups"][0]["measures"]["total"] == sum(r["amount"] for r in DATA.spending if r["spent_on"] >= since)
    assert any("@today-6d" in w for w in ans.warnings)


@pytest.mark.parametrize("spec,needle", [
    ({"entity_type": "weight", "mode": "count"}, "use source series"),  # source missing
    ({"source": "series", "entity_type": "weight", "mode": "aggregate",
      "measures": [{"name": "x", "agg": "avg", "field": "person"}]}, "needs a measure field"),
    ({"source": "series", "entity_type": "weight", "mode": "count",
      "window": [{"name": "w", "measure": "person", "size": 3, "unit": "points"}]}, "is not a measure"),
    ({"source": "series", "entity_type": "weight", "mode": "count",
      "compare": [{"measure": "kg", "op": "lt", "baseline": {"window": "nope"}}]}, "unknown window"),
    ({"source": "series", "entity_type": "weight", "mode": "aggregate",
      "group_by": [{"field": "kg"}]}, "group by a key field"),
    ({"source": "series", "entity_type": "weight", "mode": "aggregate",
      "group_by": [{"field": "person", "bucket": "month"}]}, "buckets apply to the time field"),
])
def test_series_spec_validation(k, spec, needle):
    schemas = {n: k.ledger.schemas.get(n) for n in k.ledger.schemas.names()}
    errs = validate_spec(QuerySpec.model_validate(spec), schemas)
    assert any(needle in e for e in errs), errs


def test_entity_specs_reject_series_features_but_read_first_last_dates():
    """Records: change/change_pct and first/last of a non-date field are series-only; first/last of a date field is
    its earliest/latest date (regression: haiku asked for the first and last application date with first/last)."""
    from mnemento.demo import open_demo

    led = open_demo()
    schemas = {n: led.schemas.get(n) for n in led.schemas.names()}

    def errs(measures):
        return validate_spec(QuerySpec.model_validate({"entity_type": "application", "mode": "aggregate",
                                                       "measures": measures}), schemas)

    assert any("source series only" in e for e in errs([{"name": "c", "agg": "change", "field": "applied_at"}]))
    assert any("source series only" in e for e in errs([{"name": "f", "agg": "first", "field": "platform"}]))
    assert errs([{"name": "f", "agg": "first", "field": "applied_at"}]) == []
    k = Keeper(led, ScriptedLLM())
    ans = k.ask("q", spec={"entity_type": "application", "mode": "aggregate",
                           "measures": [{"name": "f", "agg": "first", "field": "applied_at"},
                                        {"name": "l", "agg": "last", "field": "applied_at"},
                                        {"name": "lo", "agg": "min", "field": "applied_at"},
                                        {"name": "hi", "agg": "max", "field": "applied_at"}]})
    m = ans.result["groups"][0]["measures"]
    assert (m["f"], m["l"]) == (m["lo"], m["hi"]) and m["f"] < m["l"]
    led.close()

def test_interpreter_sees_series_and_answers_through_it():
    """Natural-language path: the dictionary marks the series type, the fast path leaves it alone, and the
    interpreter's series spec is answered (no LLM call on the second asking: plan cache)."""
    led = Ledger.open(":memory:")
    led.schemas.register(WEIGHT_SCHEMA)
    ingest(led, "weight", DATA.weight, by="gen", source="series_gen")
    spec = {"source": "series", "entity_type": "weight", "mode": "aggregate",
            "filters": [{"field": "person", "op": "eq", "value": "가상인"},
                        *period("measured_on", "2026-03-01", "2026-03-31")],
            "measures": [{"name": "chg", "agg": "change", "field": "kg"}], "interpretation": "3월 체중 변화"}
    llm = ScriptedLLM([{"kind": "query", "spec": spec}])
    keeper = Keeper(led, llm)
    keeper.pipeline.query_log = None
    q = "가상인 3월에 몸무게 얼마나 변했어?"
    ans = keeper.ask(q, now=NOW)
    assert ans.status == "answered" and ans.trace["path"] == "llm"
    prompt = llm.calls[0]["prompt"]
    assert "SERIES (QuerySpec source=series): one point per person and measured_on (day)" in prompt
    assert "observed values: 샘플인, 가상인" in prompt  # key values come from series_points
    rs = rows_of("weight", "2026-03-01", "2026-03-31")
    assert ans.result["groups"][0]["measures"]["chg"] == pytest.approx(rs[-1]["kg"] - rs[0]["kg"], abs=0.01)
    again = keeper.ask(q, now=NOW)
    assert again.trace["path"] == "cache" and again.result == ans.result
    led.close()


def test_fast_path_does_not_take_series_types():
    from mnemento.keeper.query.rules import parse_simple

    led = Ledger.open(":memory:")
    led.schemas.register(WEIGHT_SCHEMA)
    schemas = {n: led.schemas.get(n) for n in led.schemas.names()}
    assert parse_simple("체중 몇 개?", schemas, NOW) is None
    led.close()


def test_series_eval_answer_keys_match_reference_specs():
    """bench/series_eval.py: every reference QuerySpec reproduces its answer key (no LLM)."""
    from bench.series_eval import check

    assert check() == 0


def test_series_eval_set_grader():
    from bench.series_eval import QUESTIONS, grade

    q = next(q for q in QUESTIONS if q.grader == "set")
    data = None
    assert grade(q, data, {"items": [["hold_b", "sec_900002", "샘플전자"]]}) == (True, "ok")
    assert grade(q, data, {"items": [["hold_b", "sec_900002"], ["hold_c", "sec_900003"]]})[0]  # allowed extra
    assert not grade(q, data, {"items": [["hold_b"], ["hold_a", "sec_900001"]]})[0]  # wrong extra
    assert not grade(q, data, {"items": []})[0]


def test_a_series_without_a_key_tracks_one_thing():
    """ADR-0025: notes about one subject (my own sleep) give a series with no key field."""
    led = Ledger.open(":memory:")
    led.schemas.register({"name": "sleep", "version": 1, "kind": "series", "description": "Hours slept (fictional).",
                          "series_key": [], "time_field": "slept_on", "granularity": "day", "measures": ["hours"],
                          "fields": {"slept_on": {"type": "string", "format": "date", "description": "Night."},
                                     "hours": {"type": "number", "description": "Hours."}}})
    ingest(led, "sleep", [{"slept_on": "2026-09-01", "hours": 7.5}, {"slept_on": "2026-09-03", "hours": 6.0}],
           by="t", source="s")
    k = Keeper(led, ScriptedLLM())
    ans = k.ask("q", spec={"source": "series", "entity_type": "sleep", "mode": "aggregate",
                           "filters": period("slept_on", "2026-09-01", "2026-09-03"),
                           "measures": [{"name": "avg_h", "agg": "avg", "field": "hours"}]}, now=NOW)
    assert ans.result["groups"][0]["measures"]["avg_h"] == 6.75
    assert any("1 of 3 days have no point" in w for w in ans.warnings)
    led.close()
