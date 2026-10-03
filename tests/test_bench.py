"""Benchmark harness self-checks (no LLM): ground truth, renderers, grader and the M formatter agree.

For every question a hand-written correct QuerySpec is run through Mnemento and must be graded
correct against the generator's answer key — this catches bugs on either side.
"""

import json
from datetime import timedelta

import pytest

from bench.generate import BENCH_NOW, generate
from bench.grade import grade
from bench.questions import Question, build
from bench.render import full_context, memory_index_head, to_ledger, to_memory_dir
from bench.runners import to_answer
from mnemento.keeper import Keeper, ScriptedLLM


_REF_NOW = [BENCH_NOW]  # the reference date of the dataset under test (year of "M/D" questions)


def _d1(q):
    from mnemento.keeper.query.rules import _resolve_md

    m, d = q.text.split(" ")[0].split("/")
    return _resolve_md(int(m), int(d), None, _REF_NOW[0].date()).isoformat()


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
# v0 could not express H3 (group size filter) and H4 (ordering by event time); v1 adds having and
# order_by_event (task 0004 ②), so every question now has a reference spec.
REF["H3"] = lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "company_id"}],
                       "having": [{"measure": "count", "op": "gte", "value": 2}]}
REF["H4"] = lambda q: {"entity_type": "application", "mode": "list", "limit": 3, "descending": True,
                       "filters": [{"field": "status", "op": "eq", "value": "rejected"}],
                       "order_by_event": {"kind": "status_changed", "to": "rejected"},
                       "list_fields": ["company_id", "reason"]}
# H2 "viewed" means ever viewed: the v1 reference uses the history filter (ADR-0012)
REF["H2"] = lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
                       "filters": [{"field": "status", "op": "reached", "value": "viewed"}],
                       "measures": [{"name": "passed", "agg": "count_if",
                                     "where": [{"field": "status", "op": "eq", "value": "passed"}]}]}
NOT_EXPRESSIBLE: set[str] = set()

# evaluation set v3 unseen questions (docs/eval/unseen-v3.md): hand-written references only. W5 (a filter on the
# time between two events / two dates) is NOT expressible in QuerySpec v1.1 and is left that way on purpose
# (no feature added before the measurement); its answer key is checked against the ground truth only.
V3_NOT_EXPRESSIBLE = {"W5"}

# evaluation set v2 unseen questions (docs/eval/unseen-v2.md), hand-written references only — no system is
# run on these questions before the measurement
REF.update({
    "W1": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "platform", "op": "in", "value": ["saramin", "wanted"]},
        {"field": "applied_at", "op": "gte", "value": "@month_start(2026-12)"},
        {"field": "applied_at", "op": "lte", "value": "@month_end(2026-12)"}]},
    "W2": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000, "filters": [
        {"field": "status", "op": "eq", "value": "viewed"},
        {"field": "viewed_at", "op": "gte", "value": "@this_week_start-1w"},
        {"field": "viewed_at", "op": "lte", "value": "@today-1d"}]},
    "W3": lambda q: {"entity_type": "application", "mode": "list", "filters": [
        {"field": "status", "op": "eq", "value": "passed"},
        {"field": "applied_at", "op": "gte", "value": "2026-12-24"},
        {"field": "applied_at", "op": "lte", "value": "2027-01-03"}]},
    "W4": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
                     "measures": [{"name": "passed", "agg": "count_if",
                                   "where": [{"field": "status", "op": "eq", "value": "passed"}]}]},
    "W6": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "company_id"}],
                     "measures": [{"name": "wanted", "agg": "count_if",
                                   "where": [{"field": "platform", "op": "eq", "value": "wanted"}]},
                                  {"name": "saramin", "agg": "count_if",
                                   "where": [{"field": "platform", "op": "eq", "value": "saramin"}]}],
                     "having": [{"measure": "wanted", "op": "gte", "value": 1},
                                {"measure": "saramin", "op": "gte", "value": 1}]},
    "W7": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "applied_at", "op": "eq", "value": "@today-1d"}]},
    "W8": lambda q: {"entity_type": "application", "mode": "list", "filters": [
        {"field": "viewed_at", "op": "gte", "value": "@this_month_start"}]},
    "W9": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "status", "op": "ne", "value": "withdrawn"}]},
    "V1": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "platform", "op": "eq", "value": "groupby"},
        {"field": "applied_at", "op": "gte", "value": "@last_month_start"},
        {"field": "applied_at", "op": "lt", "value": "@this_month_start"},
        {"field": "status", "op": "reached", "value": "viewed"}]},
    "V2": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000, "filters": [
        {"field": "company_id", "op": "name_is", "value": q.text.split("에 지원한")[0]}]},
    "V3": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "company_id"}],
                     "measures": [{"name": "viewed", "agg": "count_if",
                                   "where": [{"field": "status", "op": "reached", "value": "viewed"}]}],
                     "having": [{"measure": "count", "op": "gte", "value": 2},
                                {"measure": "viewed", "op": "eq", "value": 0}]},
    "V4": lambda q: {"entity_type": "application", "mode": "list", "limit": 5, "descending": True,
                     "filters": [{"field": "status", "op": "reached", "value": "viewed"}],
                     "order_by_event": {"kind": "status_changed", "to": "viewed"}},
    "V5": lambda q: {"entity_type": "application", "mode": "aggregate", "group_by": [{"field": "platform"}],
                     "measures": [{"name": "rejected", "agg": "count_if",
                                   "where": [{"field": "status", "op": "eq", "value": "rejected"}]}]},
    "V6": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "status", "op": "in", "value": ["applied", "viewed"]},
        {"field": "applied_at", "op": "gte", "value": "2026-11-23"},
        {"field": "applied_at", "op": "lte", "value": "2026-11-29"}]},
    "V7": lambda q: {"entity_type": "application", "mode": "list", "limit": 1000,
                     "filters": [{"field": "status", "op": "eq", "value": "withdrawn"}]},
    "V8": lambda q: {"entity_type": "application", "mode": "list", "filters": [
        {"field": "platform", "op": "eq", "value": "jobkorea"}, {"field": "status", "op": "eq", "value": "passed"}]},
    "V9": lambda q: {"entity_type": "application", "mode": "count", "filters": [
        {"field": "viewed_at", "op": "gte", "value": "@this_week_start-1w"},
        {"field": "viewed_at", "op": "lte", "value": "@today-1d"}]},
})


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
    _REF_NOW[0] = ds.now
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


# ---- run loop with a fake CLI (no LLM) ----------------------------------------------------------

class FakeCLI:
    """Stands in for ClaudeCLIAdapter: B0/B1 answers, M interpretations and narrations."""

    fail = False
    calls: list = []

    def __init__(self, **kw):
        self.kw = kw
        self.model = kw.get("model", "fake")

    def complete_json(self, *, system, prompt, schema, stage):
        from mnemento.keeper.llm import LLMError, LLMResult
        from mnemento.keeper.trace import LLMUsage

        FakeCLI.calls.append((stage, bool(self.kw.get("tools"))))
        if FakeCLI.fail:
            raise LLMError("usage limit reached")
        if stage == "interpret":
            data = {"kind": "query", "spec": {"entity_type": "application", "mode": "count"}}
        elif stage == "narrate":
            data = {"answer": "prose"}
        else:
            data = {"number": 1, "ids": [], "groups": [], "flags": {"small_sample": False,
                                                                    "incomplete_period": False}, "text": "t"}
        usage = LLMUsage(stage=stage, model="fake", input_tokens=100, output_tokens=10, wall_ms=1.0,
                         model_ms=0.5, overhead_ms=0.5)
        return LLMResult(data, usage)


def test_plan_runs_resumes_and_derives_mn(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "DATA", tmp_path / "data")
    monkeypatch.setattr(br, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(br, "ClaudeCLIAdapter", FakeCLI)
    monkeypatch.setitem(br.PLANS, "t", {"description": "tiny", "reps": 2, "questions": ["D1", "D3", "U1"],
                                        "steps": [(60, ["B0", "B1", "M", "Mn"])]})
    FakeCLI.calls, FakeCLI.fail = [], True
    with pytest.raises(br.TooManyFailures):
        br.run_plan("t", "r1", "fake", None, 5)
    rows = br._load_rows(tmp_path / "results" / "r1" / "results.jsonl")
    assert len(rows) == 3 and all(r["error"] for r in rows.values())  # stopped after 3 failures

    FakeCLI.fail = False
    br.run_plan("t", "r1", "fake", None, 5)  # resume: failed rows retried, nothing else repeated
    rows = br._load_rows(tmp_path / "results" / "r1" / "results.jsonl")
    assert len(rows) == 4 * 3 * 2 and not any(r.get("error") for r in rows.values())
    mn = rows[(60, "Mn", "D3", 0)]
    m = rows[(60, "M", "D3", 0)]
    assert mn["answer"] == m["answer"] and mn["narration"] == "prose"
    assert len(mn["llm"]) == len(m["llm"]) + 1  # same interpretation + one narration call
    assert rows[(60, "Mn", "D1", 0)]["narration"] == "prose"  # fast path answers are narrated too
    n_calls = len(FakeCLI.calls)
    br.run_plan("t", "r1", "fake", None, 5)  # complete run: nothing left to do
    assert len(FakeCLI.calls) == n_calls
    assert any(tools for _, tools in FakeCLI.calls)  # B1 got its tools
    meta = json.loads((tmp_path / "results" / "r1" / "meta.json").read_text())
    assert meta["plan_description"] == "tiny" and meta["frozen_hash"] == br.frozen_hash()


def test_run_refuses_changed_frozen_files(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "RESULTS", tmp_path)
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "meta.json").write_text(json.dumps({"frozen_hash": "old", "model": "haiku"}))
    with pytest.raises(SystemExit, match="frozen files changed"):
        br._open_run("r", "haiku", None, 1, None)


def test_converter_limit_and_having():
    from types import SimpleNamespace

    q = Question("X", "unseen", "t", "number = 3; ids = those", "ids", {})
    listed = SimpleNamespace(status="answered", text="", warnings=[], evidence=["a", "b", "c"],
                             spec={"limit": 3}, result={"mode": "list", "total": 10,
                                                        "rows": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})
    assert to_answer(listed, q)["number"] == 3  # explicit limit: what is shown
    listed.spec = {}
    assert to_answer(listed, q)["number"] == 10  # default limit: the total
    grouped = SimpleNamespace(status="answered", text="", warnings=[], evidence=["x"],
                              spec={"having": [{"measure": "count", "op": "gte", "value": 2}]},
                              result={"mode": "aggregate", "total": 5, "groups": [
                                  {"group": {"company_id": "co_1"}, "n": 3, "measures": {}, "ids": []},
                                  {"group": {"company_id": "co_2"}, "n": 2, "measures": {}, "ids": []}]})
    out = to_answer(grouped, q)
    assert out["number"] == 2 and out["ids"] == ["co_1", "co_2"]


# ---- harness v2 (task 0004 ③) ---------------------------------------------------------------------

def test_guard_excludes_itself_its_children_and_its_shell():
    from bench.guard import matching

    procs = [
        {"pid": 1, "ppid": 0, "cmd": "bash -c 'python -m bench.guard --match bench.run'"},  # the guard's shell
        {"pid": 2, "ppid": 1, "cmd": "python -m bench.guard --match bench.run"},  # the guard
        {"pid": 3, "ppid": 2, "cmd": "powershell Get-CimInstance ... 'bench.run'"},  # its process listing
        {"pid": 10, "ppid": 0, "cmd": "uv run python -m bench.run plan --run r"},  # the run
        {"pid": 11, "ppid": 10, "cmd": "python -m bench.run plan --run r"},
    ]
    assert matching(procs, "bench.run", self_pid=2) == [10, 11]
    assert matching(procs[:3], "bench.run", self_pid=2) == []  # run gone -> the guard can exit


def test_max_cost_stops_cleanly(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "DATA", tmp_path / "data")
    monkeypatch.setattr(br, "RESULTS", tmp_path / "results")

    class Costly(FakeCLI):
        def complete_json(self, **kw):
            r = super().complete_json(**kw)
            r.usage.cost_usd = 0.5
            return r

    monkeypatch.setattr(br, "ClaudeCLIAdapter", Costly)
    FakeCLI.calls, FakeCLI.fail = [], False
    with pytest.raises(br.BudgetExceeded):
        br.run(60, 5, ["B0"], 3, "fake", "r", None, ["dev", "unseen"], ["D1", "D3", "U1"], max_cost=1.2)
    rows = br._load_rows(tmp_path / "results" / "r" / "results.jsonl")
    assert len(rows) == 3  # 3 x $0.5 > $1.2: stopped after the third row, which was kept


def test_hint_reaches_m_interpreter_and_meta(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "DATA", tmp_path / "data")
    monkeypatch.setattr(br, "RESULTS", tmp_path / "results")

    prompts = []

    class Recording(FakeCLI):
        def complete_json(self, *, system, prompt, schema, stage):
            prompts.append(prompt)
            return super().complete_json(system=system, prompt=prompt, schema=schema, stage=stage)

    monkeypatch.setattr(br, "ClaudeCLIAdapter", Recording)
    br.run(60, 5, ["M"], 1, "fake", "h", None, ["dev"], ["D3"], hint=True)
    assert any("Requested answer format" in p and "number = how many" in p for p in prompts)
    meta = json.loads((tmp_path / "results" / "h" / "meta.json").read_text())
    assert meta["hint"] == "same format hint for every system" and meta["eval_set"] == "v0"
    with pytest.raises(SystemExit, match="eval_set"):  # a run keeps its condition
        br.run(60, 5, ["M"], 1, "fake", "h", None, ["dev"], ["D3"], hint=False)


def test_eval_set_v2_data():
    from bench.render import memory_files

    v0, v2 = generate(400, 7), generate(400, 7, version="v2")
    assert v0.now != v2.now and v2.version == "v2"
    aliases = [a for c in v2.companies for a in c.aliases]
    assert len(aliases) == len(set(aliases)) and len(aliases) > sum(len(c.aliases) for c in v0.companies)
    labelled = [a for a in v2.apps if a.company_label]
    assert labelled and all(a.company_label in v2.company(a.company_id).aliases for a in labelled)
    text = "\n".join(body for _, body, _ in memory_files(v2))
    assert f"- 회사: {labelled[0].company_label} " in text  # written down under the other name
    qs = build(v2)
    assert [q.id for q in qs if q.set == "dev"] == [q.id for q in build(v0)]  # all 20 v0 questions are dev
    assert {q.id for q in qs if q.set == "unseen"} <= {f"V{i}" for i in range(1, 10)}
    assert generate(400, 7).apps[5].__dict__ == v0.apps[5].__dict__  # v0 unchanged by the v2 option


def test_v2_reference_specs(tmp_path):
    ds = generate(300, 11, version="v2")
    _REF_NOW[0] = ds.now
    led = to_ledger(ds, tmp_path / "v2.db")
    keeper = Keeper(led, ScriptedLLM())
    for q in build(ds):
        ans = keeper.ask(q.text, spec=REF[q.id](q), now=ds.now)
        ok, why = grade(q, to_answer(ans, q))
        assert ok, f"{q.id}: {why}"
    led.close()


@pytest.mark.parametrize("scale,seed", [(300, 11), (1500, 3)])
def test_unseen_v2_answer_keys(tmp_path, scale, seed):
    """Answer keys of V1-V9 against hand-written reference specs (no system run on the questions)."""
    ds = generate(scale, seed, version="v2")
    _REF_NOW[0] = ds.now
    qs = {q.id: q for q in build(ds) if q.set == "unseen"}
    assert {"V1", "V3", "V4", "V5", "V6", "V7", "V8", "V9"} <= set(qs)  # V2 may be excluded at a scale
    led = to_ledger(ds, tmp_path / "u.db")
    keeper = Keeper(led, ScriptedLLM())
    for q in qs.values():
        ans = keeper.ask(q.text, spec=REF[q.id](q), now=ds.now)
        ok, why = grade(q, to_answer(ans, q))
        assert ok, f"{q.id}: {why}"
    led.close()


def test_unseen_v2_rules():
    ds = generate(1500, 3, version="v2")
    qs = {q.id: q for q in build(ds)}
    v2 = qs["V2"]
    c = next(c for c in ds.companies if c.normalized == v2.text.split("에 지원한")[0])
    labels = [a.company_label for a in ds.live if a.company_id == c.id]
    assert any(labels) and not all(labels)  # some records under the alias, some under the registered name
    assert "(주)" not in v2.text and "주식회사" not in v2.text
    import dataclasses
    other_day = dataclasses.replace(ds, now=ds.now.replace(day=5))
    assert "V6" not in {q.id for q in build(other_day)}  # fixed November dates: only for 2026-12-04


def test_v2_plan_steps_reps_seed_and_whole_run_budget(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "DATA", tmp_path / "data")
    monkeypatch.setattr(br, "RESULTS", tmp_path / "results")

    class Costly(FakeCLI):
        def complete_json(self, **kw):
            r = super().complete_json(**kw)
            r.usage.cost_usd = 0.1
            return r

    monkeypatch.setattr(br, "ClaudeCLIAdapter", Costly)
    FakeCLI.calls, FakeCLI.fail = [], False
    monkeypatch.setitem(br.PLANS, "t2", {"description": "t", "reps": 2, "questions": ["D1", "D3"],
                                         "steps": [(60, ["B0"]), (60, ["B1"], 1)], "eval_set": "v2",
                                         "hint": True, "seed": 9})
    br.run_plan("t2", "p", "fake", None, 5)
    rows = br._load_rows(tmp_path / "results" / "p" / "results.jsonl")
    assert sorted({(k[1], k[3]) for k in rows}) == [("B0", 0), ("B0", 1), ("B1", 0)]  # B1 step: 1 repetition
    meta = json.loads((tmp_path / "results" / "p" / "meta.json").read_text())
    assert meta["seed"] == 9 and meta["eval_set"] == "v2"
    assert abs(br._run_cost("p") - 0.6) < 1e-9  # 6 calls x $0.1
    # resuming with more questions: the cap includes what the run already spent
    monkeypatch.setitem(br.PLANS["t2"], "questions", ["D1", "D3", "U1"])
    with pytest.raises(br.BudgetExceeded):
        br.run_plan("t2", "p", "fake", None, 5, max_cost=0.65)


def test_per_system_models_2x2(tmp_path, monkeypatch):
    import bench.run as br

    monkeypatch.setattr(br, "DATA", tmp_path / "data")
    monkeypatch.setattr(br, "RESULTS", tmp_path / "results")
    used = []

    class ModelAware(FakeCLI):
        def complete_json(self, **kw):
            used.append((self.kw.get("model"), bool(self.kw.get("tools"))))
            r = super().complete_json(**kw)
            r.usage.model = {"opus": "claude-opus-x", "haiku": "claude-haiku-x"}[self.kw["model"]]
            return r

    monkeypatch.setattr(br, "ClaudeCLIAdapter", ModelAware)
    FakeCLI.calls, FakeCLI.fail = [], False
    monkeypatch.setitem(br.PLANS, "x", {"description": "2x2", "reps": 1, "questions": ["D1", "D3"],
                                        "steps": [(60, ["M:haiku", "M:opus", "B1:haiku", "B1:opus"])],
                                        "eval_set": "v2", "hint": True, "seed": 4})
    br.run_plan("x", "x", "haiku", None, 5)
    rows = br._load_rows(tmp_path / "results" / "x" / "results.jsonl")
    assert {k[1] for k in rows} == {"M-haiku", "M-opus", "B1-haiku", "B1-opus"}
    assert ("opus", True) in used and ("haiku", True) in used and ("opus", False) in used
    meta = json.loads((tmp_path / "results" / "x" / "meta.json").read_text())
    assert meta["resolved_models"]["B1-opus"] == ["claude-opus-x"]
    assert meta["resolved_models"]["M-haiku"] == ["claude-haiku-x"]



def test_converter_event_counts_use_record_ids():
    from types import SimpleNamespace

    q = Question("X", "dev", "t", "number = how many; ids = those records", "ids", {})
    ans = SimpleNamespace(status="answered", text="", warnings=[], evidence=["evt_1"], spec={"source": "events"},
                          result={"mode": "count", "total": 1, "rows": [],
                                  "groups": [{"group": {}, "n": 1, "measures": {}, "ids": ["evt_1"],
                                              "entity_ids": ["app_dup0000"]}]})
    out = to_answer(ans, q)
    assert out["number"] == 1 and out["ids"] == ["app_dup0000"]  # regression (v2 U6): not the event id


def test_eval_set_v3(tmp_path):
    ds = generate(300, 20270201, version="v3")
    assert ds.now.date().isoformat() == "2027-02-01" and ds.now.weekday() == 0  # Monday the 1st
    aliases = [a for c in ds.companies for a in c.aliases]
    assert len(aliases) == len(set(aliases)) and any(a.company_label for a in ds.apps)
    qs = build(ds)
    assert {q.id for q in qs if q.set == "unseen"} == {f"W{i}" for i in range(1, 10)}
    assert all(q.set == "dev" for q in qs if not q.id.startswith("W"))  # v0 + v2 questions are dev
    assert "V6" not in {q.id for q in qs}  # names fixed November 2026 dates
    qs = [q for q in qs if q.id not in V3_NOT_EXPRESSIBLE]
    _REF_NOW[0] = ds.now
    led = to_ledger(ds, tmp_path / "v3.db")
    keeper = Keeper(led, ScriptedLLM())
    for q in qs:
        ok, why = grade(q, to_answer(keeper.ask(q.text, spec=REF[q.id](q), now=ds.now), q))
        assert ok, f"{q.id}: {why}"
    led.close()



def test_v3_w5_answer_key_from_ground_truth():
    """W5 has no reference spec (not expressible); check its key against a direct count instead."""
    ds = generate(1000, 20270201, version="v3")
    w5 = next(q for q in build(ds) if q.id == "W5")
    viewed = [a for a in ds.live if a.viewed_at]
    over = [a for a in viewed if a.viewed_at - a.applied > timedelta(hours=72)]
    assert w5.expected["number"] == len(over) > 0
    day_gt = [a for a in viewed if (a.viewed_at.date() - a.applied_date).days > 3]
    day_ge = [a for a in viewed if (a.viewed_at.date() - a.applied_date).days >= 3]
    assert w5.expected["alternatives"] == [len(day_gt), len(day_ge)]


def test_report_counts_no_answer_apart():
    from bench.report import render, summarize

    rows = [
        {"scale": 100, "system": "M-opus", "set": "unseen", "correct": False, "status": "clarify", "wall_ms": 10,
         "llm": [], "answer": {"number": None, "ids": [], "groups": []}},
        {"scale": 100, "system": "M-opus", "set": "unseen", "correct": False, "status": "answered", "wall_ms": 10,
         "llm": [], "answer": {"number": 3, "ids": [], "groups": []}},
    ]
    s = summarize(rows, {})
    assert s[0]["no_answer"] == 1
    assert "no answer / clarify" in render(s, {"model": "x", "run": "r", "frozen_hash": "h"})
