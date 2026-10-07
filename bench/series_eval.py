"""Series evaluation set (task 0008 step 4, ADR-0016): natural-language questions about the fictional weight and
spending series, answered by Mnemento's pipeline (M) and graded mechanically.

    python -m bench.series_eval check                    # answer keys + reference specs, no LLM
    python -m bench.series_eval run --model haiku --set dev [--run NAME]

Answers are computed from the generator's rows (bench/series_gen.py), never from Mnemento. Every question also has a
hand-written reference QuerySpec; `check` verifies that it reproduces the answer key, so a wrong key is caught before
any measurement. Unseen questions are written by someone who does not tune the interpreter and are never run before
their measurement.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from mnemento import Ledger
from mnemento.keeper import Keeper
from mnemento.series import ingest

from .series_gen import SPENDING_SCHEMA, WEIGHT_SCHEMA, SECURITIES, SeriesData, generate, generate_market

MEASURES = {*WEIGHT_SCHEMA["measures"], *SPENDING_SCHEMA["measures"], "close"}
MARKET = generate_market()
NOW = datetime.fromisoformat("2026-09-27T21:00:00+09:00")  # the generator's last day, evening
RESULTS = Path(__file__).resolve().parent / "results"


@dataclass
class SQ:
    id: str
    set: str  # dev | unseen | dropped (kept for the record, never run)
    text: str
    grader: str  # number | groups | label
    answer: Callable[[SeriesData], Any]  # number, {group label: number}, or the accepted labels (list)
    reference: dict[str, Any] | None  # a QuerySpec that answers it; None = not expressible (still measured)
    tolerance: float = 0.0
    alternatives: Callable[[SeriesData], list[Any]] | None = None  # other accepted readings
    note: str = ""


def _w(d: SeriesData, person: str, lo: str = "", hi: str = "9999") -> list[dict]:
    return sorted((r for r in d.weight if r["person"] == person and lo <= r["measured_on"] <= hi),
                  key=lambda r: r["measured_on"])


def _s(d: SeriesData, cat: str | None, lo: str = "", hi: str = "9999") -> list[dict]:
    return [r for r in d.spending if (cat is None or r["category"] == cat) and lo <= r["spent_on"] <= hi]


def _below_window(d: SeriesData, person: str, lo: str, hi: str, days: int) -> int:
    rows = _w(d, person)
    hits = 0
    for r in rows:
        if lo <= r["measured_on"] <= hi:
            day = date.fromisoformat(r["measured_on"])
            win = [x["kg"] for x in rows if day - timedelta(days=days - 1) <= date.fromisoformat(x["measured_on"]) <= day]
            hits += r["kg"] < statistics.mean(win) - 1e-9
    return hits


def _period(field_: str, lo: str, hi: str) -> list[dict]:
    return [{"field": field_, "op": "gte", "value": lo}, {"field": field_, "op": "lte", "value": hi}]


def _eq(field_: str, v: Any) -> dict:
    return {"field": field_, "op": "eq", "value": v}


QUESTIONS: list[SQ] = [
    SQ("S1", "dev", "가상인 3월 평균 몸무게 얼마였어?", "number",
       lambda d: statistics.mean(r["kg"] for r in _w(d, "가상인", "2026-03-01", "2026-03-31")),
       {"entity_type": "weight", "mode": "aggregate",
        "filters": [_eq("person", "가상인"), *_period("measured_on", "2026-03-01", "2026-03-31")],
        "measures": [{"name": "avg_kg", "agg": "avg", "field": "kg"}]}, tolerance=0.05),
    SQ("S2", "dev", "8월에 식비로 총 얼마 썼지?", "number",
       lambda d: sum(r["amount"] for r in _s(d, "식비", "2026-08-01", "2026-08-31")),
       {"entity_type": "spending", "mode": "aggregate",
        "filters": [_eq("category", "식비"), *_period("spent_on", "2026-08-01", "2026-08-31")],
        "measures": [{"name": "total", "agg": "sum", "field": "amount"}]}),
    SQ("S3", "dev", "샘플인은 6월부터 8월 말까지 몸무게가 얼마나 변했어?", "number",
       lambda d: (lambda rs: rs[-1]["kg"] - rs[0]["kg"])(_w(d, "샘플인", "2026-06-01", "2026-08-31")),
       {"entity_type": "weight", "mode": "aggregate",
        "filters": [_eq("person", "샘플인"), *_period("measured_on", "2026-06-01", "2026-08-31")],
        "measures": [{"name": "chg", "agg": "change", "field": "kg"}]}, tolerance=0.05),
    SQ("S4", "dev", "7월 지출을 카테고리별 합계로 보여줘", "groups",
       lambda d: {c: sum(r["amount"] for r in _s(d, c, "2026-07-01", "2026-07-31")) for c in ("식비", "교통", "카페")},
       {"entity_type": "spending", "mode": "aggregate", "filters": _period("spent_on", "2026-07-01", "2026-07-31"),
        "group_by": [{"field": "category"}], "measures": [{"name": "total", "agg": "sum", "field": "amount"}]}),
    SQ("S5", "dev", "9월에 카페에 하루 15000원 넘게 쓴 날이 며칠이야?", "number",
       lambda d: sum(r["amount"] > 15000 for r in _s(d, "카페", "2026-09-01", "2026-09-30")),
       {"entity_type": "spending", "mode": "count",
        "filters": [_eq("category", "카페"), *_period("spent_on", "2026-09-01", "2026-09-30"),
                    {"field": "amount", "op": "gt", "value": 15000}]},
       alternatives=lambda d: [sum(r["amount"] >= 15000 for r in _s(d, "카페", "2026-09-01", "2026-09-30"))]),
    SQ("S6", "dev", "8월에 가상인 몸무게가 최근 4주 평균보다 낮았던 날은 며칠이야?", "number",
       lambda d: _below_window(d, "가상인", "2026-08-01", "2026-08-31", 28),
       {"entity_type": "weight", "mode": "count",
        "filters": [_eq("person", "가상인"), *_period("measured_on", "2026-08-01", "2026-08-31")],
        "window": [{"name": "avg4w", "measure": "kg", "agg": "avg", "size": 28, "unit": "days"}],
        "compare": [{"measure": "kg", "op": "lt", "baseline": {"window": "avg4w"}}]}),
    SQ("S7", "dev", "올해 월별 교통비 합계 알려줘", "groups",
       lambda d: {f"2026-{m:02d}": sum(r["amount"] for r in _s(d, "교통") if r["spent_on"].startswith(f"2026-{m:02d}"))
                  for m in range(1, 10)},
       {"entity_type": "spending", "mode": "aggregate", "filters": [_eq("category", "교통")],
        "group_by": [{"field": "spent_on", "bucket": "month"}],
        "measures": [{"name": "total", "agg": "sum", "field": "amount"}]}),
    SQ("S8", "dev", "가상인 체지방률 마지막으로 잰 값이 얼마야?", "number",
       lambda d: [r for r in _w(d, "가상인") if "body_fat_pct" in r][-1]["body_fat_pct"],
       {"entity_type": "weight", "mode": "aggregate", "filters": [_eq("person", "가상인")],
        "measures": [{"name": "last_fat", "agg": "last", "field": "body_fat_pct"}]}),
    SQ("S9", "dev", "최근 일주일 동안 카페에 쓴 돈은?", "number",
       lambda d: sum(r["amount"] for r in _s(d, "카페", "2026-09-21", "2026-09-27")),
       {"entity_type": "spending", "mode": "aggregate",
        "filters": [_eq("category", "카페"), {"field": "spent_on", "op": "gte", "value": "@today-6d"}],
        "measures": [{"name": "total", "agg": "sum", "field": "amount"}]},
       alternatives=lambda d: [sum(r["amount"] for r in _s(d, "카페", "2026-09-20", "2026-09-27")),
                               sum(r["amount"] for r in _s(d, "카페", "2026-09-20", "2026-09-26"))],
       note="'최근 일주일' = today-6..today (also today-7..today, or the 7 days before today)"),
    SQ("S10", "dev", "샘플인 2분기에 가장 무거웠던 몸무게는?", "number",
       lambda d: max(r["kg"] for r in _w(d, "샘플인", "2026-04-01", "2026-06-30")),
       {"entity_type": "weight", "mode": "aggregate",
        "filters": [_eq("person", "샘플인"), *_period("measured_on", "2026-04-01", "2026-06-30")],
        "measures": [{"name": "max_kg", "agg": "max", "field": "kg"}]}),
]


def _ma_argmax(d: SeriesData, person: str, days: int, lo: str = "", hi: str = "9999",
               min_points: int = 1) -> list[str]:
    rows = _w(d, person)
    ma = {}
    for r in rows:
        if not lo <= r["measured_on"] <= hi:
            continue
        day = date.fromisoformat(r["measured_on"])
        win = [x["kg"] for x in rows if day - timedelta(days=days - 1) <= date.fromisoformat(x["measured_on"]) <= day]
        if len(win) >= min_points:
            ma[r["measured_on"]] = statistics.mean(win)
    top = max(ma.values())
    return [k for k, v in ma.items() if abs(v - top) < 1e-9]


def _month_argmax(d: SeriesData, cat: str, months: range) -> list[str]:
    tot = {f"2026-{m:02d}": sum(r["amount"] for r in _s(d, cat) if r["spent_on"].startswith(f"2026-{m:02d}"))
           for m in months}
    return [k for k, v in tot.items() if v == max(tot.values())]


# Unseen questions: written by the directing session on 2026-10-07 without seeing the code (wording kept as given).
# USED UP by run series-unseen-1 (2026-10-07): after any fix they motivate, move them to dev and ask for new ones.
# Not run by any system before their measurement; `check` only runs the reference specs, without an LLM.
QUESTIONS += [
    SQ("U1", "unseen", "가상인의 6월 평균 몸무게는 몇 kg이었어?", "number",
       lambda d: round(statistics.mean(r["kg"] for r in _w(d, "가상인", "2026-06-01", "2026-06-30")), 1),
       {"entity_type": "weight", "mode": "aggregate",
        "filters": [_eq("person", "가상인"), *_period("measured_on", "2026-06-01", "2026-06-30")],
        "measures": [{"name": "avg_kg", "agg": "avg", "field": "kg"}]}, tolerance=0.05),
    SQ("U2", "unseen", "샘플인은 올해 처음 잰 몸무게에서 가장 최근에 잰 몸무게까지 얼마나 늘었어? 줄었으면 음수로 답해줘.",
       "number", lambda d: (lambda rs: round(rs[-1]["kg"] - rs[0]["kg"], 1))(_w(d, "샘플인", "2026-01-01", "2026-09-27")),
       {"entity_type": "weight", "mode": "aggregate", "filters": [_eq("person", "샘플인")],
        "measures": [{"name": "chg", "agg": "change", "field": "kg"}]}, tolerance=0.05),
    SQ("U3", "unseen", "3분기 카페 지출은 지금까지 총 얼마야?", "number",
       lambda d: sum(r["amount"] for r in _s(d, "카페", "2026-07-01", "2026-09-27")),
       {"entity_type": "spending", "mode": "aggregate",
        "filters": [_eq("category", "카페"), *_period("spent_on", "2026-07-01", "2026-09-30")],
        "measures": [{"name": "total", "agg": "sum", "field": "amount"}]},
       note="in-progress warning expected, not graded"),
    SQ("U4", "unseen", "1월부터 8월 중에 식비를 가장 많이 쓴 달은 언제야?", "label",
       lambda d: _month_argmax(d, "식비", range(1, 9)),
       {"entity_type": "spending", "mode": "aggregate",
        "filters": [_eq("category", "식비"), *_period("spent_on", "2026-01-01", "2026-08-31")],
        "group_by": [{"field": "spent_on", "bucket": "month"}],
        "measures": [{"name": "total", "agg": "sum", "field": "amount"}], "order_by": "total", "descending": True}),
    SQ("U5", "unseen", "최근 4주 교통 결제 건수는 그 전 4주보다 몇 건 많아? 적으면 음수로.", "number",
       lambda d: (sum(r["count"] for r in _s(d, "교통", "2026-08-31", "2026-09-27"))
                  - sum(r["count"] for r in _s(d, "교통", "2026-08-03", "2026-08-30"))),
       None, note="two periods in one number: not expressible in one QuerySpec"),
    SQ("U6", "unseen", "가상인 8월 체지방률 평균은?", "number",
       lambda d: round(statistics.mean(r["body_fat_pct"] for r in _w(d, "가상인", "2026-08-01", "2026-08-31")
                                       if "body_fat_pct" in r), 1),
       {"entity_type": "weight", "mode": "aggregate",
        "filters": [_eq("person", "가상인"), *_period("measured_on", "2026-08-01", "2026-08-31")],
        "measures": [{"name": "avg_fat", "agg": "avg", "field": "body_fat_pct"}]}, tolerance=0.05,
       note="warning about rows without the field expected, not graded"),
    SQ("U7", "dropped", "5월부터 7월까지 샘플인의 7일 이동평균 몸무게가 가장 높았던 날은? 7일 중 4번 이상 잰 날만 쳐줘.",
       "label", lambda d: _ma_argmax(d, "샘플인", 7, lo="2026-05-01", hi="2026-07-31", min_points=4),
       None, note="dropped before measurement: 1st and 2nd differ by 0.007 kg (< 0.05, the agreed rule); revised twice on 2026-10-07; a minimum number of points per window is not expressible in one QuerySpec"),
    SQ("U8", "unseen", "9월에 카테고리별로 지출이 없었던 날이 각각 며칠이야?", "groups",
       lambda d: {c: 27 - len({r["spent_on"] for r in _s(d, c, "2026-09-01", "2026-09-27") if r["amount"] > 0})
                  for c in ("식비", "교통", "카페")},
       None, note="days without a point: only in the gap warning, not a query value"),
]




def _close(code: str, lo: str = "", hi: str = "9999") -> list[float]:
    return [p["close"] for p in MARKET.prices if p["security_id"] == f"sec_{code}" and lo <= p["price_on"] <= hi]


def _last_close(sid: str, at: str = "2026-09-27") -> float | None:
    pts = [p for p in MARKET.prices if p["security_id"] == sid and p["price_on"] <= at]
    return pts[-1]["close"] if pts else None


def _pnl() -> dict[str, float]:
    return {h["security_id"]: h["units"] * _last_close(h["security_id"]) - h["invested"]
            for h in MARKET.holdings.values() if _last_close(h["security_id"]) is not None}


_PNL_VALUES = [{"name": "last_close", "asof": {"field": "security_id", "series": "price", "measure": "close"}},
               {"name": "mv", "expr": {"op": "mul", "args": ["units", "last_close"]}},
               {"name": "pnl", "expr": {"op": "sub", "args": ["mv", "invested"]}}]

QUESTIONS += [  # task 0008 step 5 (issue #2), dev — written with the code
    SQ("P1", "dev", "가상바이오 8월 평균 종가는?", "number",
       lambda d: statistics.mean(_close("900001", "2026-08-01", "2026-08-31")),
       {"entity_type": "price", "mode": "aggregate",
        "filters": [_eq("security_id", "sec_900001"), *_period("price_on", "2026-08-01", "2026-08-31")],
        "measures": [{"name": "avg_close", "agg": "avg", "field": "close"}]}, tolerance=0.5),
    SQ("P2", "dev", "보유 종목별 평가 손익 보여줘", "groups", lambda d: _pnl(),
       {"entity_type": "holding", "mode": "list", "values": _PNL_VALUES, "list_fields": ["security_id", "pnl"]},
       tolerance=1),
    SQ("P3", "dev", "보유 종목 평가 손익 합계는 얼마야?", "number", lambda d: sum(_pnl().values()),
       {"entity_type": "holding", "mode": "aggregate", "values": _PNL_VALUES,
        "measures": [{"name": "total_pnl", "agg": "sum", "field": "pnl"}]}, tolerance=1),
    SQ("P4", "dev", "샘플전자 산 날 종가는 얼마였어?", "number",
       lambda d: _last_close("sec_900002", MARKET.trades["trade_b"]["traded_at"]),
       {"entity_type": "trade", "mode": "list", "filters": [_eq("security_id", "sec_900002")],
        "values": [{"name": "close_then", "asof": {"field": "security_id", "series": "price", "measure": "close",
                                                   "at": "traded_at"}}]}),
]


def open_ledger(data: SeriesData) -> Ledger:
    led = Ledger.open(":memory:")
    led.schemas.register(WEIGHT_SCHEMA)
    led.schemas.register(SPENDING_SCHEMA)
    ingest(led, "weight", data.weight, by="series_gen", source="series_gen")
    ingest(led, "spending", data.spending, by="series_gen", source="series_gen")
    # step 5: fictional securities, prices, holdings and trades (examples/investment schemas)
    led.schemas.load_dir(Path(__file__).resolve().parents[1] / "examples" / "investment")
    at = "2026-07-01T09:00:00+09:00"
    for code, name in SECURITIES:
        led.record_event(f"sec_{code}", "created", {"code": code, "name": name}, at, "series_gen", None,
                         entity_type="security")
    for kind, docs in (("holding", MARKET.holdings), ("trade", MARKET.trades)):
        for i, doc in docs.items():
            led.record_event(i, "created", doc, at, "series_gen", None, entity_type=kind)
    ingest(led, "price", MARKET.prices, by="series_gen", source="series_gen")
    return led


def to_answer(ans) -> dict[str, Any]:
    """KeeperAnswer -> {number, groups, label}, mechanically: count/list -> total; one group -> its first measure;
    groups -> label: value. label = the first listed row's date, or the group with the largest first measure."""
    out: dict[str, Any] = {"number": None, "groups": {}, "label": None}
    if ans.status != "answered" or not ans.result:
        return out
    res = ans.result
    if res.get("mode") in ("count", "list"):
        out["number"] = res["total"]
        rows = res.get("rows") or []
        values = [v["name"] for v in (ans.spec or {}).get("values") or []]
        if res.get("mode") == "list" and values:  # computed columns: the last one is the asked value, per record
            key = lambda r: r.get("security_id") or r["id"]  # noqa: E731
            out["groups"] = {key(r): r.get(values[-1]) for r in rows}
            if len(rows) == 1:
                out["number"] = rows[0].get(values[-1])
            return out
        # "the last value": one row shown with one measure in it -> that value (dev S8, haiku listed the latest row)
        shown = [c for c in (rows[0] if len(rows) == 1 else {}) if c in MEASURES]
        if res.get("mode") == "list" and len(shown) == 1 and (ans.spec or {}).get("limit") == 1:
            out["number"] = rows[0][shown[0]]
        if rows:
            out["label"] = next((v for k, v in rows[0].items() if k != "id" and isinstance(v, str)
                                 and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", v)), None)
        return out
    measures = [m["name"] for m in (ans.spec or {}).get("measures") or []]
    for g in res.get("groups") or []:
        v = g["measures"].get(measures[0]) if measures else g["n"]
        label = "/".join(str(x) for x in g["group"].values())
        out["groups"][label] = v
    if len(res.get("groups") or []) == 1:
        out["number"] = next(iter(out["groups"].values()))
    valued = [(v, k) for k, v in out["groups"].items() if v is not None]
    if valued:
        out["label"] = max(valued)[1]
    return out


def grade(q: SQ, data: SeriesData, got: dict[str, Any]) -> tuple[bool, str]:
    exp = q.answer(data)
    if q.grader == "number":
        n = got.get("number")
        if n is None:
            return False, "no number"
        targets = [exp, *(q.alternatives(data) if q.alternatives else [])]
        if any(abs(float(n) - float(t)) <= q.tolerance + 1e-9 for t in targets):
            return True, "ok"
        return False, f"number {n} != {exp}"
    if q.grader == "label":
        return (True, "ok") if got.get("label") in exp else (False, f"label {got.get('label')} not in {exp}")
    groups = got.get("groups") or {}
    for label, v in exp.items():
        g = groups.get(label)
        if g is None and v:
            return False, f"missing group {label}"
        if g is not None and abs(float(g) - float(v)) > q.tolerance + 1e-9:
            return False, f"{label}: {g} != {v}"
    return True, "ok"


def check() -> int:
    """Answer keys vs the reference specs, run through the pipeline without an LLM."""
    from mnemento.keeper import ScriptedLLM

    data = generate()
    led = open_ledger(data)
    k = Keeper(led, ScriptedLLM())
    k.pipeline.query_log = None
    bad = 0
    for q in QUESTIONS:
        if q.reference is None:
            print(f"{q.id:4} {q.set:6} --  no reference spec ({q.note})  key={_short(q.answer(data))}")
            continue
        kind = led.schemas.get(q.reference["entity_type"]).kind
        ans = k.ask(q.text, spec={"source": "series" if kind == "series" else "entities", **q.reference}, now=NOW)
        ok, why = grade(q, data, to_answer(ans))
        bad += not ok
        print(f"{q.id:4} {q.set:6} {'OK ' if ok else 'BAD'} {why:30} key={_short(q.answer(data))}")
    led.close()
    return bad


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, float) else f"{v:.4f}"
    return s if len(s) < 70 else s[:67] + "..."


def run(model: str, which: str, run_id: str, ids: list[str] | None = None) -> None:
    from mnemento.keeper.llm import ClaudeCLIAdapter

    data = generate()
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.jsonl"
    done = set()
    if path.exists():
        done = {(r["model"], r["id"]) for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    led = open_ledger(data)
    k = Keeper(led, ClaudeCLIAdapter(model=model))
    k.pipeline.query_log = None
    k.pipeline.cache = None  # every question interpreted, as in the entity benchmark
    with path.open("a", encoding="utf-8") as fh:
        for q in [q for q in QUESTIONS if q.set == which and (not ids or q.id in ids)]:
            if (model, q.id) in done:
                continue
            ans = k.ask(q.text, now=NOW)
            got = to_answer(ans)
            ok, why = grade(q, data, got)
            cost = sum((u.get("cost_usd") or 0) for u in ans.trace.get("llm", []))
            row = {"model": model, "id": q.id, "set": q.set, "question": q.text, "ok": ok, "why": why,
                   "expected": q.answer(data), "got": got, "status": ans.status, "spec": ans.spec,
                   "result": {**(ans.result or {}), "rows": ((ans.result or {}).get("rows") or [])[:20]},
                   "warnings": ans.warnings, "cost_usd": cost, "ms": ans.trace["totals"]["total_ms"]}
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            print(f"M-{model} {q.id}: {'OK ' if ok else 'BAD'} {why:40} {row['ms'] / 1000:.1f}s ${cost:.3f}")
    led.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("run")
    r.add_argument("--model", default="haiku")
    r.add_argument("--set", default="dev", choices=["dev", "unseen"])
    r.add_argument("--run", default=None)
    r.add_argument("--ids", default="", help="comma-separated question ids")
    a = p.parse_args(argv)
    if a.cmd == "check":
        sys.exit(1 if check() else 0)
    run(a.model, a.set, a.run or f"series-{a.set}", [i for i in a.ids.split(",") if i])


if __name__ == "__main__":
    main()
