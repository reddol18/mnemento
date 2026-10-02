"""Benchmark harness self-checks (no LLM): ground truth, renderers, grader and the M formatter agree.

For every question a hand-written correct QuerySpec is run through Mnemento and must be graded
correct against the generator's answer key — this catches bugs on either side.
"""

import json

import pytest

from bench.generate import BENCH_NOW, generate
from bench.grade import grade
from bench.questions import Question, build
from bench.render import full_context, memory_index_head, to_ledger, to_memory_dir
from bench.runners import to_answer
from mnemento.keeper import Keeper, ScriptedLLM


def _d1(q):
    m, d = q.text.split(" ")[0].split("/")
    return f"{BENCH_NOW.year}-{int(m):02d}-{int(d):02d}"


REF = {
    "D1": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "platform", "op": "eq", "value": "saramin"},
        {"field": "applied_at", "op": "eq", "value": _d1(q)}]},
    "D2": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000, "filters": [
        {"field": "company_id", "op": "name_is", "value": q.text.split(" 예전에")[0]}]},
    "D3": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000,
                     "filters": [{"field": "status", "op": "eq", "value": "viewed"}]},
    "D4": lambda q: {"entity_type": "application", "mode": "aggregate", "filters": [
        {"field": "expected_rate", "op": "in", "value": ["top10", "top30"]},
        {"field": "applied_at", "op": "gte", "value": "@last_month_start"}],
        "group_by": [{"field": "applied_at", "bucket": "month"}, {"field": "expected_rate"}],
        "measures": [{"name": "viewed", "agg": "count_if", "where": [{"field": "viewed_at", "op": "exists"}]}]},
    "D5": lambda q: {"entity_type": "application", "mode": "aggregate",
                     "filters": [{"field": "viewed_at", "op": "exists"}],
                     "measures": [{"name": "hours", "agg": "avg_hours_between_events",
                                   "event_from": {"kind": "created"},
                                   "event_to": {"kind": "status_changed", "to": "viewed"}}]},
    "D6": lambda q: {"source": "events", "entity_type": "application", "mode": "list",
                     "filters": [{"field": "kind", "op": "eq", "value": "corrected"}]},
    "D7": lambda q: {"entity_type": "application", "mode": "list", "filters": [
        {"field": "reason", "op": "contains", "value": "서치펌"},
        {"field": "status", "op": "eq", "value": "withdrawn"}]},
    "U1": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "platform", "op": "eq", "value": "wanted"},
        {"field": "applied_at", "op": "gte", "value": "@last_month_start"},
        {"field": "applied_at", "op": "lt", "value": "@this_month_start"}]},
    "U2": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000, "filters": [
        {"field": "company_id", "op": "name_is", "value": q.text.split(" 쪽에")[0]}]},
    "U3": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "status", "op": "eq", "value": "rejected"}, {"field": "platform", "op": "eq", "value": "saramin"}]},
    "U4": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "status", "op": "eq", "value": "applied"},
        {"field": "applied_at", "op": "lte", "value": "@today-7d"}]},
    "U5": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
                     "measures": [{"name": "passed", "agg": "count_if",
                                   "where": [{"field": "status", "op": "eq", "value": "passed"}]}]},
    "U6": lambda q: {"source": "events", "entity_type": "application", "mode": "list",
                     "filters": [{"field": "kind", "op": "eq", "value": "retracted"}]},
    "U7": lambda q: {"entity_type": "application", "mode": "aggregate", "measures": [
        {"name": "days", "agg": "avg_days_between", "field": "applied_at", "field_end": "viewed_at"}]},
    "U8": lambda q: {"entity_type": "application", "mode": "list", "filters": [
        {"field": "viewed_at", "op": "eq", "value": "@today-1d"}]},
    # questions written independently by the directing agent
    "H1": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000, "filters": [
        {"field": "platform", "op": "eq", "value": "wanted"}, {"field": "status", "op": "eq", "value": "applied"},
        {"field": "applied_at", "op": "gte", "value": "@today-14d"}]},
    "H2": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
                     "filters": [{"field": "viewed_at", "op": "exists"}],
                     "measures": [{"name": "passed", "agg": "count_if",
                                   "where": [{"field": "status", "op": "eq", "value": "passed"}]}]},
    "H5": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "status", "op": "in", "value": ["applied", "viewed"]},
        {"field": "applied_at", "op": "lt", "value": "@today-30d"}]},
}
# Not expressible in QuerySpec v0 (found while writing these references, deliberately not added so the
# benchmark measures the system as frozen): H3 needs a filter on group size (HAVING count >= 2),
# H4 needs ordering by the time of an event (the rejection).
NOT_EXPRESSIBLE = {"H3", "H4"}


@pytest.fixture(scope="module", params=[(120, 1), (400, 7)], ids=["n120", "n400"])
def world(request, tmp_path_factory):
    scale, seed = request.param
    ds = generate(scale, seed)
    d = tmp_path_factory.mktemp(f"bench{scale}")
    led = to_ledger(ds, d / "m.db")
    yield ds, led, d
    led.close()


def test_reference_specs_reproduce_the_answer_key(world):
    ds, led, _ = world
    keeper = Keeper(led, ScriptedLLM())
    for q in build(ds):
        if q.id in NOT_EXPRESSIBLE:
            continue
        ans = keeper.ask(q.text, spec=REF[q.id](q), now=BENCH_NOW)
        ok, why = grade(q, to_answer(ans, q))
        assert ok, f"{q.id}: {why}\n{ans.text[:400]}"


def test_question_sets_and_formats(world):
    ds, _, _ = world
    qs = build(ds)
    assert [q.id for q in qs if q.set == "dev"] == [f"D{i}" for i in range(1, 8)]
    assert [q.id for q in qs if q.set == "unseen"] == [f"U{i}" for i in range(1, 9)] + [f"H{i}" for i in range(1, 6)]
    assert all(q.format for q in qs)


def test_memory_rendering(world):
    ds, _, d = world
    mem = to_memory_dir(ds, d / "memory")
    files = list(mem.glob("*.md"))
    assert len(files) == len(ds.apps) + len({a.company_id for a in ds.apps}) + 1  # + MEMORY.md
    head = memory_index_head(mem)
    index_lines = (mem / "MEMORY.md").read_text(encoding="utf-8").count("\n")
    assert head.count("\n") <= 201
    assert ("more lines truncated" in head) == (index_lines > 200)  # like Claude Code: only 200 lines load
    ctx = full_context(ds)
    assert all(a.id in ctx for a in ds.apps)
    assert "무효" in ctx and "정정" in ctx  # retractions and corrections are visible to B0/B1 too


def test_grader_rules():
    q = Question("X", "dev", "t", "f", "ids", {"number": 2, "ids": ["a", "b"]})
    assert grade(q, {"number": 2, "ids": ["b", "a"]})[0]
    assert not grade(q, {"number": 2, "ids": ["a"]})[0]
    assert not grade(q, {"number": 3, "ids": ["a", "b"]})[0]
    q = Question("Y", "dev", "t", "f", "number", {"number": 10.0, "tolerance": 0.5, "alternatives": [12]})
    assert grade(q, {"number": 10.4})[0] and grade(q, {"number": 12})[0] and not grade(q, {"number": 11})[0]
    q = Question("Z", "dev", "t", "f", "rate_groups",
                 {"groups": {("2026-11", "top10"): (4, 0.5)}, "flags": {"small_sample": True}})
    good = {"groups": [{"period": "2026-11", "category": "상위 10%", "n": 4, "value": 50}],
            "flags": {"small_sample": True}}
    assert grade(q, good)[0]
    assert not grade(q, {**good, "flags": {"small_sample": False}})[0]


def test_generator_is_deterministic():
    a, b = generate(150, 3), generate(150, 3)
    dump = lambda ds: repr([x.__dict__ for x in build(ds)])  # noqa: E731
    assert dump(a) == dump(b)
    c = generate(150, 4)
    assert [x.applied for x in c.apps[:5]] != [x.applied for x in a.apps[:5]]


def test_report_summary_and_overhead_exclusion():
    from bench.report import render, summarize

    u = {"input_tokens": 3000, "output_tokens": 500, "cache_read_input_tokens": 1000,
         "cache_creation_input_tokens": 0, "cost_usd": 0.01, "overhead_ms": 2000}
    rows = [
        {"scale": 100, "system": "M", "set": "dev", "correct": True, "wall_ms": 6000, "llm": [u]},
        {"scale": 100, "system": "M", "set": "unseen", "correct": False, "wall_ms": 20, "llm": []},
        {"scale": 1000, "system": "B0", "set": "dev", "correct": False, "wall_ms": 0, "llm": [],
         "error": "context limit exceeded (not measurable)"},
    ]
    s = summarize(rows, {"overhead_input_tokens": {"plain": 2500, "tools": 6700}})
    m = next(x for x in s if x["system"] == "M")
    assert m["dev"] == (1, 1) and m["unseen"] == (0, 1)
    assert m["in_per_q"] == 2000 and m["in_per_q_no_overhead"] == 750  # (4000 - 2500) / 2
    assert m["time_mean_no_overhead"] == (4.0 + 0.02) / 2
    assert next(x for x in s if x["system"] == "B0").get("not_measurable")
    md = render(s, {"model": "haiku", "run": "t", "frozen_hash": "x"})
    assert "not measurable" in md and "1/1 (100%)" in md
